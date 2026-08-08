"""The two arguments that decide whether a weekend costs $24k or $48k, held to the file.

``.github/workflows/block-launch-fleet.yml`` is dispatched once, by one person, against a
purchase that cannot be cancelled, with no rehearsal. Every property below is one whose
absence produces no error anywhere: the launch succeeds, the instances come up, the console
looks right, and the only trace is a bill weeks later.

**THE THREE MUTATIONS THAT MOTIVATE MOST OF THIS MODULE.** Drop
``--instance-market-options``, and eight on-demand machines start beside a paid block. Drop
the reservation target, and the same. Add a cluster placement group, which every piece of
general AWS guidance about multi-node GPU training tells you to do, and the launch is refused
with ``InsufficientInstanceCapacity`` against capacity you are staring at and have paid for.

The rest is the seam nothing else can see. Every OIDC role in this account pins
``job_workflow_ref`` to a literal path, so renaming any of these workflow files silently revokes
its ability to reach AWS at all -- and the moment that is discovered is the moment somebody
dispatches, which is the morning of the window.

**THE TWO FILES ADDED FOR THE END OF THE WINDOW CARRY A PROPERTY OF THEIR OWN.** The flush that
saves ``/scratch`` is a systemd timer on the nodes rather than a scheduled workflow, because a
scheduled workflow is delivered late and the deadline is AWS terminating the fleet against a
wall clock. That is a decision somebody tidying up would reverse -- a cron in a file is more
visible than a timer inside a bootstrap -- so it is held here as well as argued there.
"""

from __future__ import annotations

import ast
import gzip
import re
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import pytest
import yaml
from workflow_support import (
    WORKFLOWS_ROOT,
    aws_commands,
    load_workflow,
    only_job,
    run_step_script,
    shell_syntax_without_heredoc_bodies,
    step,
    unreal_context_references,
    write_stub,
)

from edullm_platform.block_drain import DRAIN_FROM_MINUTES, RECLAIM_MARGIN_MINUTES
from edullm_platform.config import load_yaml
from edullm_platform.contracts.inventory import OrganizationInventory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAUNCH_FILE = "block-launch-fleet.yml"
RUN_FILE = "block-run.yml"
DRAIN_FILE = "block-drain.yml"
LOGS_FILE = "block-logs.yml"
DISTRIBUTED_FILE = "block-run-distributed.yml"
LAUNCH_PATH = WORKFLOWS_ROOT / LAUNCH_FILE
RUN_PATH = WORKFLOWS_ROOT / RUN_FILE
DRAIN_PATH = WORKFLOWS_ROOT / DRAIN_FILE
LOGS_PATH = WORKFLOWS_ROOT / LOGS_FILE
BOOTSTRAP_PATH = PROJECT_ROOT / "infra" / "block-node-bootstrap.sh"
ROLE_TEMPLATE = PROJECT_ROOT / "infra" / "iam" / "block-fleet-roles.yaml"
STATUS_TOOL = PROJECT_ROOT / "tools" / "block_status.py"

LAUNCH_STEP = "Launch the nodes that are not already up"
RESERVATION_STEP = "Refuse a reservation that is not open for business"
RESOLVE_STEP = "Resolve the image, the subnet, the security group, the cards and the root device"
VERIFY_STEP = "Verify every instance is drawing from the block, or terminate all of them"
FABRIC_STEP = "Verify every node came up with the fabric and an address, or refuse the fleet"
GUARD_STEP = "Refuse a hand-started launch from somebody who may not make one"

#: Every workflow file in this lane. One role serves all of them -- see the template for why
#: splitting it would suggest a boundary that does not exist -- and this tuple is the thing the
#: trust policy has to agree with in both directions.
BLOCK_WORKFLOWS = (LAUNCH_FILE, RUN_FILE, DRAIN_FILE, LOGS_FILE, DISTRIBUTED_FILE)

#: The variable each of them names for the role it assumes.
ROLE_VARIABLE = "AWS_BLOCK_FLEET_ROLE_ARN"

#: Each ``cat > <path> <<'DELIMITER'`` the bootstrap writes a script with. ``bash -n`` over the
#: bootstrap does not read inside a quoted heredoc -- the body is literal text to the parser --
#: so the several hundred lines of shell it installs on the machine are unchecked unless
#: something pulls them out first.
INSTALLED_SCRIPT = re.compile(
    r"^cat > (?P<path>/usr/local/bin/[a-z-]+) <<'(?P<delimiter>[A-Z]+)'\n"
    r"(?P<body>.*?)\n(?P=delimiter)\n",
    re.MULTILINE | re.DOTALL,
)

#: Every ``${EDULLM_BLOCK_X:?...}`` the bootstrap refuses to start without.
REQUIRED_SETTING = re.compile(r"\$\{(EDULLM_BLOCK_[A-Z_]+):\?")

#: What EC2 accepts as user-data, in raw form, before it is base64-encoded. Not a round number
#: this repository chose: it is the documented ``RunInstances`` bound, and exceeding it is an
#: ``InvalidParameterValue`` on the first launch rather than anything subtler.
EC2_USER_DATA_LIMIT = 16384

#: ``uv run python - a b`` with the first two words dropped, so what remains is ``python - a b``
#: and the heredoc on stdin is still the script. The sibling workflow modules shift three
#: because their steps name a file rather than reading one from stdin.
UV_PASSTHROUGH = 'shift 2\nexec "${PYTHON_EXECUTABLE}" "$@"\n'

#: Answers ``describe-instances`` with an empty account so that every node reads as missing, and
#: records the argv of everything else. The launch is the call under test, so what it was handed
#: is the output of this stub rather than a side effect of it.
AWS_RECORDING_STUB = """
if [ "${2:-}" = describe-instances ]; then
  echo '{"Reservations": []}'
  exit 0
fi
printf '%s\\n' "$@" >> "${RECORDED}"
echo '{}'
"""

# --------------------------------------------------------------------------------------
# WHAT THIS LANE ASKS AWS FOR, AGAINST WHAT ITS ROLE IS ALLOWED TO DO.
#
# The gap this closes cost the window it was found in. `block-launch-fleet.yml` asks the
# registry whether the image tag exists, in the resolve step, ahead of the subnet and the
# security group and the interface plan -- and `BlockFleetRole` held eight statements, none of
# them naming an `ecr:` action. That is not a risk, it is an `AccessDeniedException` on the
# first dispatch, before a single machine starts, against a reservation that began billing a
# minute earlier and cannot be refunded.
#
# Nothing could have caught it by reading one file. The workflow is correct on its own terms
# and so is the template; what is wrong is the seam, and the seam is only visible to something
# that reads both. Every other test in this module holds a property of one artifact.
#
# THE CHECK IS AT SERVICE AND VERB LEVEL AND THAT IS DELIBERATE. Resource and condition
# dimensions are where an IAM grant is subtlest, and a checker that tried to evaluate them
# would be a policy simulator with a bug in it. A missing *action* is the coarsest possible
# failure and the one that actually happens, because an action is what somebody forgets to add
# when they add a call. The region dimension of the one cross-region grant here is held
# separately below, by name.
# --------------------------------------------------------------------------------------

#: An ``aws <service> <verb>`` anywhere in a run body. Matched mid-line as well as at the start
#: of one, because ``if ! aws ec2 wait ...`` is how the launch waits for its own fleet.
AWS_CALL = re.compile(r"(?<![\w./-])aws\s+(?P<service>[a-z0-9-]+)\s+(?P<verb>[a-z0-9-]+)")

#: The same word on its own, used to check that every ``aws`` in a body was read as a call. A
#: form this regex cannot parse would otherwise drop out of the comparison silently, which is
#: the failure mode of every checker like this one.
AWS_WORD = re.compile(r"(?<![\w./-])aws(?=\s)")

SHELL_COMMENT = re.compile(r"^[ \t]*#.*$", re.MULTILINE)

#: Every tool a run body reaches for. Half this lane makes no ``aws`` call in YAML at all --
#: `block-logs.yml`, `block-drain.yml` and `block-status.yml` each run one Python tool -- so a
#: check that read only the workflows would report those three as needing nothing.
TOOL_INVOCATION = re.compile(r"tools/(?P<tool>[a-z0-9_]+)\.py")

#: The first word of a CLI argument list, when that word is a service. Used to find the calls
#: the tools make, which are lists rather than command lines.
AWS_SERVICES = frozenset(
    {"cloudformation", "ec2", "ecr", "iam", "logs", "s3", "s3api", "secretsmanager", "ssm", "sts"}
)

#: Where the CLI name and the IAM name differ.
IAM_SERVICE_PREFIX = {"s3api": "s3"}

#: Calls whose IAM action is not the CamelCase of the verb, with the reason each is here.
CALL_AUTHORISED_BY = {
    # A waiter is not an API call. It polls one, and for `instance-running` that is
    # DescribeInstances -- which is why the launch could add the wait without touching IAM.
    ("ec2", "wait"): "ec2:DescribeInstances",
    # S3 authorises a listing against the bucket rather than against an operation named after
    # the CLI verb, and the ListObjectsV2 spelling appears in no policy anywhere.
    ("s3api", "list-objects-v2"): "s3:ListBucket",
    # HeadObject is GetObject with the body discarded and is authorised as GetObject. A policy
    # granting `s3:HeadObject` grants nothing at all, which is the trap this entry names.
    ("s3api", "head-object"): "s3:GetObject",
}

#: Calls that need no grant. ``sts get-caller-identity`` is available to every principal and
#: cannot be granted, so requiring it in the policy would fail this check forever.
CALL_NEEDS_NO_GRANT = frozenset({("sts", "get-caller-identity")})


def iam_action(service: str, verb: str) -> str | None:
    """The IAM action one ``aws <service> <verb>`` is authorised by, or ``None`` for the free ones."""
    if (service, verb) in CALL_NEEDS_NO_GRANT:
        return None
    known = CALL_AUTHORISED_BY.get((service, verb))
    if known is not None:
        return known
    operation = "".join(part[:1].upper() + part[1:] for part in verb.split("-"))
    return f"{IAM_SERVICE_PREFIX.get(service, service)}:{operation}"


def calls_in_shell(script: str) -> set[tuple[str, str]]:
    """Every AWS call a run body makes itself, with the heredocs and the comments taken out.

    Comments go because this lane writes long ones and they name the calls they are about -- a
    paragraph explaining why there is no ``aws s3 sync`` here would otherwise read as one.

    Heredoc bodies go for a sharper reason, and the first draft of this check found it:
    ``block-run.yml`` prints ``aws ssm start-session --target ...`` out of a Python heredoc, as
    the line a person types on their own laptop to get a shell on the node they were given.
    That is somebody else calling AWS with their own credentials. Reading it as a call this
    role makes would demand ``ssm:StartSession`` on a workflow that never opens a session, and
    a grant added to satisfy a checker is the worst kind of grant there is.
    """
    body = SHELL_COMMENT.sub("", shell_syntax_without_heredoc_bodies(script))
    found = {(match["service"], match["verb"]) for match in AWS_CALL.finditer(body)}
    assert len(AWS_CALL.findall(body)) == len(AWS_WORD.findall(body)), (
        "an aws invocation in this body is written in a form this check cannot read, so it "
        "would be left out of the comparison rather than fail it:\n" + body
    )
    return found


def calls_in_tool(source: str) -> set[tuple[str, str]]:
    """Every AWS call a tool makes, read off the argument lists it builds.

    ``aws_json(["ec2", "describe-instances", ...])`` and its siblings are the only shape any of
    these use, and reading the literal rather than the helper name means a tool that reaches
    for a second helper is still covered.
    """
    found: set[tuple[str, str]] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.List) or len(node.elts) < 2:
            continue
        service, verb = node.elts[0], node.elts[1]
        if not isinstance(service, ast.Constant) or not isinstance(service.value, str):
            continue
        if not isinstance(verb, ast.Constant) or not isinstance(verb.value, str):
            continue
        if service.value in AWS_SERVICES:
            found.add((service.value, verb.value))
    return found


def block_lane_calls() -> dict[str, set[tuple[str, str]]]:
    """Every AWS call the lane makes, by the file that makes it, workflows and tools alike."""
    made: dict[str, set[tuple[str, str]]] = {}
    for name in BLOCK_WORKFLOWS:
        path = WORKFLOWS_ROOT / name
        bodies = [
            str(item.get("run", "")) for item in only_job(load_workflow(path))["steps"]
        ]
        for body in bodies:
            made.setdefault(name, set()).update(calls_in_shell(body))
        for tool in {found for body in bodies for found in TOOL_INVOCATION.findall(body)}:
            source = PROJECT_ROOT / "tools" / f"{tool}.py"
            assert source.is_file(), f"{name} runs {source} and it is not there"
            made.setdefault(f"{name} -> tools/{tool}.py", set()).update(
                calls_in_tool(source.read_text(encoding="utf-8"))
            )
    return made


def fleet_role_statements() -> list[dict[str, Any]]:
    template = yaml.safe_load(ROLE_TEMPLATE.read_text(encoding="utf-8"))
    statements = template["Resources"]["BlockFleetRole"]["Properties"]["Policies"][0][
        "PolicyDocument"
    ]["Statement"]
    assert isinstance(statements, list)
    return statements


def fleet_role_grants() -> set[str]:
    return {
        action
        for statement in fleet_role_statements()
        if statement["Effect"] == "Allow"
        for action in (
            statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]]
        )
    }


@pytest.fixture(scope="module")
def launch() -> dict[str, Any]:
    return load_workflow(LAUNCH_PATH)


@pytest.fixture(scope="module")
def runner() -> dict[str, Any]:
    return load_workflow(RUN_PATH)


def launch_command(launch: dict[str, Any]) -> list[str]:
    script = step(only_job(launch), LAUNCH_STEP)["run"]
    matching = [
        command for command in aws_commands(script) if command[:3] == ["aws", "ec2", "run-instances"]
    ]
    assert len(matching) == 1, "the launch step makes something other than one run-instances call"
    return matching[0]


def test_the_launch_names_the_market_type_that_draws_from_a_block(launch: dict[str, Any]) -> None:
    """Mutation: drop ``--instance-market-options``.

    Without it the launch is an ordinary on-demand launch and EC2 has no reason to hand it a
    reservation it was not asked for. It does not fail. It starts a p5.48xlarge at roughly $55
    an hour next to a block already paid for in full, and nothing anywhere says so.
    """
    argv = launch_command(launch)

    assert "--instance-market-options" in argv
    assert argv[argv.index("--instance-market-options") + 1] == "MarketType=capacity-block"


def test_the_launch_names_the_reservation_by_id(launch: dict[str, Any]) -> None:
    """Mutation: drop the reservation target, or replace it with a preference.

    A capacity block is a *targeted* reservation, so the launch has to name it. The sibling
    property ``CapacityReservationPreference`` takes ``open`` or ``none``, is mutually
    exclusive with a target, and setting ``open`` here is the double bill written down as
    configuration -- EC2 would use any reservation that happens to match, which for a targeted
    block is none of them.
    """
    argv = launch_command(launch)
    target = argv[argv.index("--capacity-reservation-specification") + 1]

    assert target == "CapacityReservationTarget={CapacityReservationId=${RESERVATION_ID}}"
    assert "CapacityReservationPreference" not in " ".join(argv)


def test_no_placement_group_reaches_the_launch(launch: dict[str, Any]) -> None:
    """Mutation: add one, on the advice of every multi-node GPU training guide AWS publishes.

    Capacity block instances are already delivered into an UltraCluster, so a placement group
    on top makes the launch a request EC2 has to satisfy inside a group it did not reserve
    against. What comes back is ``InsufficientInstanceCapacity`` against your own paid
    reservation, which reads as AWS having failed to deliver the block.
    """
    body = LAUNCH_PATH.read_text(encoding="utf-8")

    assert "--placement" not in body
    assert "placement-group" not in body


def test_the_launch_names_its_network_interfaces_rather_than_a_subnet(
    launch: dict[str, Any],
) -> None:
    """THE MUTATION THAT COSTS MOST OF THE PURCHASE AND RAISES NOTHING ANYWHERE.

    ``--subnet-id`` and ``--network-interfaces`` are mutually exclusive, and the subnet form is
    the one this launch used to carry. It produces a ``p5.48xlarge`` with a single ordinary ENA
    interface and *no EFA device at all* -- the AMI still has the driver and the plugin, the
    instance type still advertises 3,200 Gbps, and NCCL still forms its rings over TCP sockets
    between machines. The loss curve is correct. The step time is several times what it should
    be, on a non-refundable block bought for one 64-rank job.

    Reverting to ``--subnet-id`` is a one-token diff that reads as a simplification, which is
    why it is held here rather than left to the comment above it.
    """
    argv = launch_command(launch)

    assert "--subnet-id" not in argv
    assert "--network-interfaces" in argv
    assert argv[argv.index("--network-interfaces") + 1] == "${interfaces[@]}"


def test_the_interface_list_is_built_by_the_library_a_test_can_reach(
    launch: dict[str, Any],
) -> None:
    """Mutation: write the thirty-three interfaces into the workflow by hand.

    The layout is position-sensitive in a way no reviewer checks -- device index 1 on card 0 and
    device index 0 on the rest, an ordinary interface first because an ``efa-only`` one cannot
    be primary -- and every number in it belongs to the shape rather than to this file.
    ``tests/test_block_fleet.py`` is where that is held, and it can only hold it while the
    workflow calls into the module instead of restating it.
    """
    script = step(only_job(launch), RESOLVE_STEP)["run"]

    assert "from edullm_platform.block_fleet import" in script
    assert "interface_plan(" in script
    # Every number that decides the layout comes out of describe-instance-types, so none of the
    # counts this shape happens to have may be written here.
    assert "NetworkCardIndex" not in script


def test_the_launch_refuses_a_security_group_that_does_not_carry_efa(
    launch: dict[str, Any],
) -> None:
    """THE SILENT HALF OF ATTACHING AN EFA, AND IT IS NOT ON THE INSTANCE.

    EFA traffic is matched on the security group and requires one admitting all traffic to and
    from itself. Without that rule every device still attaches, ``ibv_devinfo`` still lists
    them, and every packet between nodes is dropped -- NCCL falls back to sockets and nothing
    reports a problem, which is the same outcome as never having asked for the fabric.

    The group used to attach implicitly, because a launch naming no group at all gets the VPC
    default. Naming interfaces means naming a group on each one, so it is resolved and checked
    rather than assumed.
    """
    script = step(only_job(launch), RESOLVE_STEP)["run"]

    assert script.count("aws ec2 describe-security-groups") == 1
    assert "Name=group-name,Values=default" in script
    assert "admits_its_own_members(" in script
    assert "Groups=" not in script, "the group belongs on the interfaces the library builds"


def test_the_fabric_check_refuses_the_fleet_and_does_not_terminate_it(
    launch: dict[str, Any],
) -> None:
    """Mutation: copy the reservation check wholesale, terminate included.

    That check kills the fleet because leaving it up costs $55 an hour per machine. A fleet that
    came up without its EFA devices is on the reservation, costs nothing extra, and is worth
    more alive than tidy -- and wiring a terminate-everything to a count this launch has never
    once produced in anger is a larger risk than the fault it reacts to.
    """
    script = step(only_job(launch), FABRIC_STEP)["run"]

    assert "without_the_fabric(" in script
    assert "unaddressable(" in script
    assert "terminate-instances" not in script
    assert "block_fleet_came_up_without_the_fabric" in script


def test_the_expected_interface_count_is_the_one_the_launch_used(launch: dict[str, Any]) -> None:
    """Mutation: type the expected count into the check.

    A verification that asserts a number somebody typed rather than the number the launch was
    built from is checking that two humans agree, which is the thing that is never true at 06:00
    on a Saturday. The count is written by the step that builds the list and read by the step
    that reads the fleet back.
    """
    resolve = step(only_job(launch), RESOLVE_STEP)["run"]
    fabric = step(only_job(launch), FABRIC_STEP)["run"]

    assert "efa-expected.txt" in resolve
    assert "plan.efa" in resolve
    assert "efa-expected.txt" in fabric


def test_the_interfaces_the_library_built_are_the_ones_run_instances_is_handed(
    launch: dict[str, Any], tmp_path: Path
) -> None:
    """THE SEAM NO ASSERTION ABOUT THE FILE CAN SEE, SO THE STEP IS RUN.

    One step writes the interface list and the next reads it into a bash array. Between them is
    ``mapfile`` and a ``"${interfaces[@]}"`` expansion under ``set -u``, and every way that can
    go wrong produces a plausible-looking command: an unsplit blob EC2 rejects, a silently empty
    array that launches with no interfaces at all, a quoting slip that hands the CLI one
    argument containing thirty-three commas. None of them is visible in the YAML.

    So the launch step is executed the way the runner executes it, with ``aws`` recording what
    it was handed, and what the interface builder produced is compared against what arrived.
    """
    plan_arguments = [
        f"NetworkCardIndex={card},DeviceIndex={device},SubnetId=subnet-0abc,"
        f"Groups=sg-0abc,InterfaceType={kind},DeleteOnTermination=true"
        for card, device, kind in ((0, 0, "interface"), (0, 1, "efa-only"), (1, 0, "efa-only"))
    ]
    for name, contents in {
        "ami-id.txt": "ami-0abc",
        "root-device.txt": "/dev/sda1",
        "gpu-count.txt": "8",
        "account-id.txt": "123456789012",
        "ends-at.txt": "2026-08-12T11:30:00Z",
        "reclaim-minutes.txt": str(RECLAIM_MARGIN_MINUTES),
        "drain-from-minutes.txt": str(DRAIN_FROM_MINUTES),
        "network-interfaces.txt": "\n".join(plan_arguments),
    }.items():
        (tmp_path / name).write_text(contents + "\n", encoding="utf-8")

    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "block-node-bootstrap.sh").write_text(
        BOOTSTRAP_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    recorded = tmp_path / "recorded.txt"
    stub_bin = tmp_path / "bin"
    write_stub(stub_bin, "uv", UV_PASSTHROUGH)
    write_stub(stub_bin, "aws", AWS_RECORDING_STUB)

    result = run_step_script(
        step(only_job(launch), LAUNCH_STEP)["run"],
        cwd=tmp_path,
        env={
            "RUNNER_TEMP": str(tmp_path),
            "RECORDED": str(recorded),
            "PYTHON_EXECUTABLE": sys.executable,
            "PYTHONPATH": str(PROJECT_ROOT / "src"),
            "RESERVATION_ID": "cr-0afc33f3a1af417a7",
            "INSTANCE_TYPE": "p5.48xlarge",
            "INSTANCE_COUNT": "1",
            "BLOCK_REGION": "us-east-2",
            "IMAGE_REGION": "us-east-1",
            "IMAGE_REPOSITORY": "repository",
            "IMAGE_TAG": "tag",
            "INSTANCE_PROFILE": "profile",
            "OUTPUTS_BUCKET": "outputs",
            "DATA_BUCKET": "data",
            "WANDB_SECRET_ID": "secret",
            "LOG_SYNC_SECONDS": "60",
            "ROOT_VOLUME_GIB": "500",
        },
        stub_bin=stub_bin,
    )

    assert result.returncode == 0, result.stderr
    argv = recorded.read_text(encoding="utf-8").splitlines()
    handed = argv[argv.index("--network-interfaces") + 1 : argv.index("--instance-market-options")]

    assert argv[:2] == ["ec2", "run-instances"]
    assert handed == plan_arguments
    assert "--subnet-id" not in argv


def test_the_metadata_hop_limit_reaches_a_container(launch: dict[str, Any]) -> None:
    """Mutation: leave ``--metadata-options`` off and take the default.

    The training command runs in a Docker container on the default bridge network, one hop
    further from the metadata service than the host. At the default limit of one the container
    gets no credentials at all, and what that looks like is a run that trains for hours and
    then cannot write a checkpoint -- discovered at the end rather than the start.
    """
    argv = launch_command(launch)
    options = argv[argv.index("--metadata-options") + 1]

    assert "HttpPutResponseHopLimit=2" in options
    assert "HttpTokens=required" in options


def test_every_instance_is_tagged_with_its_node_and_its_block(launch: dict[str, Any]) -> None:
    """The node tag is how ``block-run.yml`` addresses a machine and the block tag is how
    everything else selects the fleet. A launch that omitted either would produce a fleet
    nothing else in this lane can find."""
    argv = launch_command(launch)
    tags = argv[argv.index("--tag-specifications") + 1]

    assert "Key=edullm:node,Value=${node}" in tags
    assert "Key=edullm:block,Value=${RESERVATION_ID}" in tags


def test_the_verification_terminates_and_then_fails(launch: dict[str, Any]) -> None:
    """Mutation: raise inside the Python instead of writing a file the shell reads.

    ``set -e`` would end the step at that raise, before the termination -- so the workflow
    would report the failure loudly and leave eight on-demand machines running, which is the
    exact outcome the check exists to prevent. The verdict is written to a file and the shell
    decides, which is why the refusal is four lines below the terminate rather than above it.
    """
    script = step(only_job(launch), VERIFY_STEP)["run"]
    terminate = [
        command
        for command in aws_commands(script)
        if command[:3] == ["aws", "ec2", "terminate-instances"]
    ]

    assert len(terminate) == 1
    assert script.index("terminate-instances") < script.index("block_launch_was_not_reserved")
    assert "exit 1" in script


def test_a_fleet_that_never_answered_is_not_reported_as_ready(launch: dict[str, Any]) -> None:
    """Mutation: gate the workflow on ``not-ready.txt`` being empty, which is what it did.

    That file holds the nodes that answered and were not ready. A node that never answered at
    all -- an agent that never registered, or a fleet whose launch step did nothing -- produces
    no Systems Manager invocation and therefore no line in it, so an empty file means either
    every node is ready or nothing is there. The version gated on emptiness reported a green
    launch for a fleet that had not come up.
    """
    steps = only_job(launch)["steps"]
    wait = step(only_job(launch), "Wait for every node to finish its own bootstrap")["run"]
    final = steps[-1]["run"]

    assert "fleet-ready.txt" in wait
    assert "fleet-ready.txt" in final
    assert 'verdict}" != "yes"' in final
    assert "block_nodes_never_became_ready" in final


def test_the_verification_reads_the_library_a_test_can_reach(launch: dict[str, Any]) -> None:
    """Mutation: inline the comparison as ``if not instance.get(...)``.

    That version is invisible to review and passes an instance drawing from somebody else's
    block. ``tests/test_block_fleet.py`` is where the comparison is actually held, and it can
    only hold it while the workflow calls into the module rather than restating it.
    """
    script = step(only_job(launch), VERIFY_STEP)["run"]

    assert "from edullm_platform.block_fleet import" in script
    assert "unreserved(" in script


def test_the_launch_guard_is_first_and_names_the_roster_admins(launch: dict[str, Any]) -> None:
    """Below the checkout it is a refusal that has already done work; as a job-level ``if`` it
    is a skip, which renders beside a tick and tells the person who pressed the button they
    succeeded. Both mutations are what the three deploy workflows are held against, and this
    one guards eight machines rather than a stack reconciliation.

    Unconditional rather than gated on the event, which is where it differs from those three.
    They also fire on a push to ``main``, and refusing that path would strand an approved
    template with no way to land; this file has one trigger, so a condition naming it would be
    a claim about a second path that does not exist.
    """
    job = only_job(launch)
    guard = step(job, GUARD_STEP)
    accepted = {
        part
        for line in guard["run"].splitlines()
        if line.strip().endswith(") ;;")
        for part in line.strip().removesuffix(" ;;").strip().rstrip(")").split("|")
    }
    admins = load_yaml(PROJECT_ROOT / "config" / "organization.yaml", OrganizationInventory).admins

    assert job["steps"][0]["name"] == GUARD_STEP
    assert "if" not in job
    assert "if" not in guard
    assert set(launch["on"]) == {"workflow_dispatch"}
    assert accepted == set(admins)


def test_starting_a_run_is_deliberately_not_limited_to_admins(runner: dict[str, Any]) -> None:
    """THE ONE PROPERTY SOMEBODY TIDYING THIS UP WOULD BREAK FIRST.

    ``block-launch-fleet.yml`` is admin-only because it spends money, and copying that guard
    onto this file looks like consistency. It would lock roughly fifteen of the thirty-five
    people here out of the only door they have: they hold no AWS role, so a workflow that
    holds the credential is not a convenience for them, it is the whole access path.
    """
    job = only_job(runner)
    names = [item.get("name", "") for item in job["steps"]]

    assert not any("may not make one" in name for name in names)
    assert "if" not in job


def test_a_run_is_refused_on_a_node_somebody_else_holds(runner: dict[str, Any]) -> None:
    """The claim file on the machine is the lock and the shared sheet is the minutes. Two
    dispatches ninety seconds apart do not queue on eight cards: both runs start, both
    allocate, and both die."""
    script = step(only_job(runner), "Find the node and read what it is doing")["run"]

    assert "node_is_busy:" in script
    assert "node_has_unclaimed_work:" in script
    assert "take_the_node_anyway" in script


def test_the_run_workflow_clones_rather_than_building_an_image(runner: dict[str, Any]) -> None:
    """Mutation: reintroduce a per-commit image build for reproducibility.

    The reproducibility argument is a good one and it does not reach this lane: nothing here
    produces an admission record, a lineage entry or a citable run, so there is no record for a
    pinned digest to make trustworthy. What there is is seventy-two hours in which eight people
    iterate hourly, and a publish cycle per commit would spend a large part of it watching a
    registry.
    """
    body = RUN_PATH.read_text(encoding="utf-8")

    assert "docker build" not in body
    assert "edullm-node" in body
    assert "run.yaml" in body


def test_every_block_workflow_assumes_the_role_the_template_names_for_it() -> None:
    """THE SEAM NO SINGLE FILE CAN SEE, AND THE ONE THAT FAILS ON THE SATURDAY.

    Every OIDC role in this account pins ``job_workflow_ref`` to a literal workflow path at
    ``refs/heads/main``. Renaming or moving any of these files does not fail anything here, in
    review, or at merge -- it fails at ``AssumeRole``, at the moment of the one dispatch that
    matters, with a message about a subject claim rather than about a filename.

    Adding a file fails the same way and is the likelier direction now that there are four.
    The trust list is an enumeration, so a workflow whose path is not in it holds no AWS
    identity at all until somebody re-applies the stack from a laptop.
    """
    trust = ROLE_TEMPLATE.read_text(encoding="utf-8")

    for name in BLOCK_WORKFLOWS:
        assert f".github/workflows/{name}@refs/heads/main" in trust, name
        assert (WORKFLOWS_ROOT / name).is_file()
        assert ROLE_VARIABLE in (WORKFLOWS_ROOT / name).read_text(encoding="utf-8"), name


def test_nothing_else_in_the_tree_assumes_the_block_fleet_role() -> None:
    """The trust names four files and this is the other half of that claim. A fifth workflow
    reaching for the variable is one whose token the role will refuse, which presents as a
    broken credentials step rather than as a policy that has not been widened."""
    reaching = sorted(
        path.name
        for path in WORKFLOWS_ROOT.glob("*.yml")
        if ROLE_VARIABLE in path.read_text(encoding="utf-8")
    )

    assert reaching == sorted(BLOCK_WORKFLOWS)


def test_the_reporting_workflows_may_read_the_outputs_bucket_and_not_write_it() -> None:
    """Mutation: reuse the node role's S3 statement, which already grants what is needed.

    It grants far more: ``PutObject`` and ``DeleteObject`` over the bucket holding every
    checkpoint of the window. The drain report and the log reader consume that bucket and
    produce nothing in it, and a report is exactly the kind of thing that quietly acquires a
    write when somebody copies a statement rather than narrowing one.
    """
    template = yaml.safe_load(ROLE_TEMPLATE.read_text(encoding="utf-8"))
    statements = template["Resources"]["BlockFleetRole"]["Properties"]["Policies"][0][
        "PolicyDocument"
    ]["Statement"]
    reading = [item for item in statements if item["Sid"] == "ReadWhatTheFleetWrote"]
    granted = {
        action
        for statement in statements
        for action in (
            statement["Action"]
            if isinstance(statement["Action"], list)
            else [statement["Action"]]
        )
    }

    assert len(reading) == 1
    assert set(reading[0]["Action"]) == {"s3:GetObject", "s3:ListBucket", "s3:GetBucketLocation"}
    assert not {"s3:PutObject", "s3:DeleteObject"} & granted


def test_the_launch_supplies_every_setting_the_bootstrap_refuses_to_start_without() -> None:
    """Mutation: add a ``${EDULLM_BLOCK_NEW_THING:?}`` to the bootstrap and not to the launch.

    The bootstrap runs unattended, once, on a machine nobody is watching. An unset setting
    fails at the first line of user-data, so the node never registers with Systems Manager and
    never writes a failure record either -- it is simply a machine that boots, bills for the
    window, and answers nothing. Reading the demands out of the script rather than listing
    them here is what makes the two sides impossible to drift apart.
    """
    demanded = set(REQUIRED_SETTING.findall(BOOTSTRAP_PATH.read_text(encoding="utf-8")))
    supplied = set(
        re.findall(
            r"printf '(EDULLM_BLOCK_[A-Z_]+)=",
            step(only_job(load_workflow(LAUNCH_PATH)), LAUNCH_STEP)["run"],
        )
    )

    assert demanded, "the bootstrap demands nothing, so this test is guarding nothing"
    assert demanded <= supplied, f"the launch never sets {sorted(demanded - supplied)}"


def test_the_user_data_the_launch_builds_fits_inside_what_ec2_accepts() -> None:
    """THE ONE THAT WOULD HAVE FAILED ON THE SATURDAY, AND IT IS A LENGTH.

    EC2 refuses user-data above 16,384 bytes in raw form. The bootstrap plus its prelude is
    around 25 KB, nearly all of it the comments that make the file worth having, so the first
    ``run-instances`` fails with ``InvalidParameterValue`` -- loudly, which is the good part,
    at 11:31 UTC on the morning the window opens, which is not.

    Compressed it is a quarter of that, and cloud-init decompresses gzip user-data before
    executing it. This measures the committed file so that a change trebling its length fails
    on a pull request rather than at the launch, and it measures the compressed size because
    that is the number EC2 is actually shown.
    """
    prelude = "\n".join(
        f"{name}=value" for name in REQUIRED_SETTING.findall(BOOTSTRAP_PATH.read_text("utf-8"))
    )
    composed = f"#!/bin/bash\n{prelude}\n{BOOTSTRAP_PATH.read_text(encoding='utf-8')}"
    compressed = len(gzip.compress(composed.encode("utf-8"), compresslevel=9))
    script = step(only_job(load_workflow(LAUNCH_PATH)), LAUNCH_STEP)["run"]

    assert compressed <= EC2_USER_DATA_LIMIT, (
        f"the bootstrap compresses to {compressed} bytes against a limit of "
        f"{EC2_USER_DATA_LIMIT}. Move it to S3 and fetch it from a short stub."
    )
    assert "gzip -9" in script, "the launch sends the bootstrap uncompressed and it will not fit"
    assert 'fileb://${user_data}.gz' in script
    assert str(EC2_USER_DATA_LIMIT) in script, "the launch does not check the bound before it"


def test_the_launch_injects_the_bootstrap_file_rather_than_a_copy_of_it() -> None:
    """Mutation: paste the bootstrap into the workflow as a heredoc.

    Inlined, it stops being a file ``bash -n`` reads and becomes a 300-line quoted string
    inside YAML -- and user-data is the one artifact in this lane that nobody can watch fail.
    """
    script = step(only_job(load_workflow(LAUNCH_PATH)), LAUNCH_STEP)["run"]

    assert "cat infra/block-node-bootstrap.sh" in script
    assert BOOTSTRAP_PATH.is_file()


def test_the_node_role_can_reach_both_buckets_and_the_registry_it_pulls_from() -> None:
    """The nodes are in one region and the registry they pull from is in the other. ECR
    authorisation is per-region, so a grant naming the fleet region matches nothing and the
    symptom is a pull refused against a repository that plainly exists."""
    template = yaml.safe_load(ROLE_TEMPLATE.read_text(encoding="utf-8"))
    node_role = template["Resources"]["BlockNodeRole"]["Properties"]
    statements = node_role["Policies"][0]["PolicyDocument"]["Statement"]
    granted = {statement["Sid"] for statement in statements}

    assert granted >= {
        "WriteCheckpointsAndLogs",
        "ReadTheCorpusMirror",
        "PullTheTrainingImageAcrossRegions",
        "ReadTheSharedWeightsAndBiasesKey",
    }
    assert "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore" in node_role["ManagedPolicyArns"]
    assert "PermissionsBoundary" in node_role


def test_the_fleet_role_may_pass_only_the_node_role() -> None:
    """Mutation: widen ``iam:PassRole`` to ``*``.

    That is the ordinary privilege escalation through PassRole: a role that may attach any
    instance profile in the account to a machine it starts holds everything any of those
    profiles hold.
    """
    template = yaml.safe_load(ROLE_TEMPLATE.read_text(encoding="utf-8"))
    statements = template["Resources"]["BlockFleetRole"]["Properties"]["Policies"][0][
        "PolicyDocument"
    ]["Statement"]
    passing = [
        statement for statement in statements if statement.get("Action") == "iam:PassRole"
    ]

    named = template["Resources"]["BlockNodeRole"]["Properties"]["RoleName"]

    assert len(passing) == 1
    assert passing[0]["Resource"]["Fn::Sub"].endswith(f":role/{named}")


def test_the_status_tool_can_reach_the_nodes_through_the_role_a_workflow_lends_it() -> None:
    """``tools/block_status.py`` runs from a laptop under a maintainer session most of the
    time, and under this role when a workflow runs it with ``--no-profile``. The four Systems
    Manager actions it makes are granted here or it exits 2 on an access denial, which reads as
    a broken fleet rather than as a policy that was never widened."""
    template = yaml.safe_load(ROLE_TEMPLATE.read_text(encoding="utf-8"))
    statements = template["Resources"]["BlockFleetRole"]["Properties"]["Policies"][0][
        "PolicyDocument"
    ]["Statement"]
    granted = {
        action
        for statement in statements
        if statement["Effect"] == "Allow"
        for action in (
            statement["Action"]
            if isinstance(statement["Action"], list)
            else [statement["Action"]]
        )
    }

    assert STATUS_TOOL.is_file()
    assert granted >= {
        "ec2:DescribeInstances",
        "ssm:SendCommand",
        "ssm:ListCommandInvocations",
        "ssm:GetCommandInvocation",
    }


def test_every_aws_call_this_lane_makes_is_an_action_the_role_allows() -> None:
    """THE SEAM BETWEEN A WORKFLOW AND ITS POLICY, AND IT HAS ALREADY BEEN WRONG ONCE.

    ``block-launch-fleet.yml`` checked the training image tag with ``aws ecr describe-images``,
    in the resolve step, under ``set -euo pipefail``, ahead of everything the launch needs --
    and ``BlockFleetRole`` named no ``ecr:`` action at all. Both files reviewed clean. The
    result was an access denial on the first dispatch, before a machine started, a minute into
    a window that had begun billing and could not be refunded.

    A missing action is the coarsest thing an IAM grant can get wrong and the one that
    genuinely happens, because an action is what somebody forgets when they add a call. So
    this reads every ``aws`` the lane makes -- in the run bodies and in the tools those bodies
    run, which is where three of the six workflows keep all of theirs -- maps each to the
    action that authorises it, and asks the template.
    """
    granted = fleet_role_grants()
    missing = sorted(
        f"{source} calls `aws {service} {verb}`, which needs {action}"
        for source, calls in block_lane_calls().items()
        for service, verb in sorted(calls)
        for action in [iam_action(service, verb)]
        if action is not None
        and not any(fnmatch(action, allowed) for allowed in granted)
    )

    assert not missing, (
        "these calls are refused by the role the lane assumes, which fails as an "
        "AccessDeniedException at the moment of the dispatch:\n" + "\n".join(missing)
    )


def test_the_cross_check_reads_the_tools_a_workflow_runs_and_not_only_its_yaml() -> None:
    """A checker that matched nothing would pass the test above and prove nothing.

    Half this lane makes no AWS call in YAML: `block-logs.yml`, `block-drain.yml` and
    `block-status.yml` each run one Python tool and pass it a bucket. A reader that stopped at
    the run bodies would report those three as needing no grant whatsoever, which is the
    reading under which the S3 statement is dead weight and gets removed.

    The three calls named here are one from each direction -- a workflow call, a tool call, and
    the S3 spelling whose IAM action is not its CLI verb.
    """
    made = block_lane_calls()
    everything = {call for calls in made.values() for call in calls}

    assert ("ecr", "describe-images") in made[LAUNCH_FILE]
    assert ("s3api", "list-objects-v2") in everything
    assert ("ssm", "send-command") in everything
    assert iam_action("s3api", "list-objects-v2") == "s3:ListBucket"
    assert iam_action("ec2", "run-instances") == "ec2:RunInstances"
    assert iam_action("sts", "get-caller-identity") is None


def test_the_registry_grant_names_the_region_the_registry_is_in() -> None:
    """The resource dimension of the one grant here that crosses a region, held by name.

    The fleet is in us-east-2 and the repository it pre-pulls from is in us-east-1. ECR
    authorisation is per-region, so a grant naming the fleet region matches nothing, and the
    denial reads as the repository being absent rather than as the policy being wrong -- which
    is the wrong thing to be debugging with eight machines already billing. The node role has
    carried this property since it was written and the fleet role now needs it too.
    """
    checking = [
        statement
        for statement in fleet_role_statements()
        if statement["Action"] == "ecr:DescribeImages"
    ]

    assert len(checking) == 1
    assert checking[0]["Resource"]["Fn::Sub"] == (
        "arn:${AWS::Partition}:ecr:${ImageRegion}:${AWS::AccountId}:repository/"
        "${TrainingRepository}"
    )
    assert "ecr:GetAuthorizationToken" not in fleet_role_grants(), (
        "DescribeImages is authorised on its own action. A token grant takes Resource: * and "
        "buys nothing for a call that never asks for one"
    )


def test_the_flush_is_a_timer_on_the_node_and_not_a_cron_in_a_workflow() -> None:
    """THE DECISION THIS WHOLE MODULE IS LEAST ABLE TO ARGUE FOR ITSELF, SO IT IS PINNED.

    A cron in a YAML file is more visible than a systemd unit inside a three-hundred-line
    bootstrap, so moving the flush into ``block-drain.yml`` reads as a simplification and looks
    right in a diff. It is not: a scheduled GitHub Actions run is delivered minutes late under
    load with no committed bound, and what is waiting on the other side of this deadline is AWS
    terminating eight machines against a wall clock. Anything not in S3 at that minute never
    existed.

    So the schedule in the workflow drives a *report* and the flush is on the machine holding
    the data. What this checks is that the timer is still what saves anything: the workflow may
    read the fleet, and the bootstrap has to install the unit that does the copying.
    """
    bootstrap = BOOTSTRAP_PATH.read_text(encoding="utf-8")
    scripts = "".join(
        str(item.get("run", "")) for item in only_job(load_workflow(DRAIN_PATH))["steps"]
    )

    assert "edullm-block-drain.timer" in bootstrap
    assert "WantedBy=timers.target" in bootstrap
    assert "systemctl enable --now edullm-block-drain.timer" in bootstrap
    assert "aws s3 sync" in bootstrap
    # Read out of the run bodies rather than off the file, so that the paragraph above -- which
    # names the thing it is forbidding -- does not satisfy the rule it is arguing for.
    assert "aws s3 sync" not in scripts
    assert "tools/block_drain.py" in scripts


def test_the_launch_carries_the_window_end_off_the_reservation_onto_every_node() -> None:
    """Mutation: leave the deadline out and have the node work it out, or write it in a file.

    The node holds no grant to ask EC2 anything, so "work it out" means adding one to the
    instance role for a single read. A date in a file is worse: it is correct for one block and
    silently wrong for the next one bought, and what silently wrong means here is a fleet whose
    drain never fires.
    """
    reservation = step(only_job(load_workflow(LAUNCH_PATH)), RESERVATION_STEP)["run"]
    launch = step(only_job(load_workflow(LAUNCH_PATH)), LAUNCH_STEP)["run"]

    assert 'row.get("EndDate")' in reservation
    assert "reservation_carries_no_end_date" in reservation
    assert "ends-at.txt" in reservation
    assert "printf 'EDULLM_BLOCK_ENDS_AT=%s\\n'" in launch


def test_the_reclaim_margin_the_nodes_are_given_comes_out_of_the_library() -> None:
    """Mutation: type ``30`` into the launch workflow, which is what it is today.

    Then the number lives in three places -- the module the report reads it from, the workflow,
    and every node's settings file -- and the two that a change would miss are the two nobody
    re-reads. The bound that decides when a fleet starts saving itself is not a number to keep
    two copies of.
    """
    reservation = step(only_job(load_workflow(LAUNCH_PATH)), RESERVATION_STEP)["run"]

    assert "from edullm_platform.block_drain import" in reservation
    assert "RECLAIM_MARGIN_MINUTES" in reservation
    assert "DRAIN_FROM_MINUTES" in reservation
    assert str(RECLAIM_MARGIN_MINUTES) not in reservation.replace("2010-09-09", "")
    assert str(DRAIN_FROM_MINUTES) not in reservation


def test_the_drain_schedule_never_stops_anybody_s_run() -> None:
    """THE ONE IRREVERSIBLE THING IN THIS FILE, AND IT IS BEHIND A DISPATCH INPUT.

    ``--stop-runs`` asks the trainer in every container to shut down so that OLMo-core writes
    its final checkpoint whole rather than being cut off. That is right in the last minutes of
    a window and catastrophic four times an hour for three days, and the failure would present
    as everybody's runs mysteriously dying rather than as a workflow being wrong.

    The default on the input is the control, and a schedule supplies no inputs at all -- so
    what this holds is that the flag is reached through one and defaults to false.
    """
    workflow = load_workflow(DRAIN_PATH)
    body = step(only_job(workflow), "Drain every node and read back what landed")["run"]

    assert workflow["on"]["workflow_dispatch"]["inputs"]["stop_runs"]["default"] is False
    assert "schedule" in workflow["on"]
    assert '"${STOP_RUNS}" = "true"' in body


def test_reading_a_log_is_deliberately_not_limited_to_admins() -> None:
    """The property somebody tidying this up would break first, and it is the same one
    ``block-run.yml`` carries. These two workflows exist because roughly fifteen of the
    thirty-five people here hold no AWS role; an admin guard on either hands the door back to
    the twenty who never needed it."""
    for path in (DRAIN_PATH, LOGS_PATH):
        job = only_job(load_workflow(path))
        names = [item.get("name", "") for item in job["steps"]]

        assert not any("may not make one" in name for name in names), path.name
        assert "if" not in job, path.name


def test_every_expression_in_the_two_reporting_workflows_names_something_real() -> None:
    """GitHub resolves an unknown property on a known context to the empty string rather than
    failing, so a plausible typo surfaces as an unexplained AssumeRole failure -- which for a
    reader with no AWS access is indistinguishable from the platform being broken."""
    assert unreal_context_references(DRAIN_PATH) == []
    assert unreal_context_references(LOGS_PATH) == []


@pytest.mark.slow
@pytest.mark.parametrize(
    "installed",
    [
        pytest.param(found, id=found.group("path").rsplit("/", 1)[-1])
        for found in INSTALLED_SCRIPT.finditer(BOOTSTRAP_PATH.read_text(encoding="utf-8"))
    ],
)
def test_the_scripts_the_bootstrap_installs_on_the_machine_parse_as_bash(
    installed: re.Match[str], tmp_path: Path
) -> None:
    """THE GAP ``bash -n infra/block-node-bootstrap.sh`` LEAVES, AND IT IS MOST OF THE FILE.

    A quoted heredoc body is literal text to the parser, so the several hundred lines of shell
    the bootstrap writes into ``/usr/local/bin`` are never syntax-checked by anything that
    checks the bootstrap. They run unattended, once, on a machine nobody is watching, and the
    first evidence of an unbalanced quote in the drain would be a fleet that never saved
    itself.
    """
    script = tmp_path / installed.group("path").rsplit("/", 1)[-1]
    script.write_text(installed.group("body") + "\n", encoding="utf-8")

    checked = subprocess.run(
        ["bash", "-n", str(script)], check=False, capture_output=True, text=True
    )

    assert checked.returncode == 0, checked.stderr


def test_the_helper_the_drain_tool_calls_is_a_verb_the_helper_answers_to() -> None:
    """Mutation: rename the verb on one side.

    ``tools/block_drain.py`` sends a literal command string over Systems Manager, so a rename
    fails as a non-zero invocation on all eight nodes at once, in a report whose whole job is
    to say whether the nodes are safe -- and it fails identically to a fleet that is genuinely
    broken.
    """
    bootstrap = BOOTSTRAP_PATH.read_text(encoding="utf-8")
    tool = (PROJECT_ROOT / "tools" / "block_drain.py").read_text(encoding="utf-8")

    assert "status | claim | release | run | logs | drain)" in bootstrap
    assert "drain) do_drain " in bootstrap
    assert '"edullm-node drain --stop-runs" if stop_runs else "edullm-node drain"' in tool
