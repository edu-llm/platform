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
``job_workflow_ref`` to a literal path, so renaming either workflow file silently revokes its
ability to reach AWS at all -- and the moment that is discovered is the moment somebody
dispatches, which is the morning of the window.
"""

from __future__ import annotations

import gzip
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from workflow_support import WORKFLOWS_ROOT, aws_commands, load_workflow, only_job, step

from edullm_platform.config import load_yaml
from edullm_platform.contracts.inventory import OrganizationInventory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAUNCH_FILE = "block-launch-fleet.yml"
RUN_FILE = "block-run.yml"
LAUNCH_PATH = WORKFLOWS_ROOT / LAUNCH_FILE
RUN_PATH = WORKFLOWS_ROOT / RUN_FILE
BOOTSTRAP_PATH = PROJECT_ROOT / "infra" / "block-node-bootstrap.sh"
ROLE_TEMPLATE = PROJECT_ROOT / "infra" / "iam" / "block-fleet-roles.yaml"
STATUS_TOOL = PROJECT_ROOT / "tools" / "block_status.py"

LAUNCH_STEP = "Launch the nodes that are not already up"
VERIFY_STEP = "Verify every instance is drawing from the block, or terminate all of them"
GUARD_STEP = "Refuse a hand-started launch from somebody who may not make one"

#: The variable both workflows name for the role they assume. One name, because one role
#: serves both files -- see the template for why splitting it would suggest a boundary that
#: does not exist.
ROLE_VARIABLE = "AWS_BLOCK_FLEET_ROLE_ARN"

#: Every ``${EDULLM_BLOCK_X:?...}`` the bootstrap refuses to start without.
REQUIRED_SETTING = re.compile(r"\$\{(EDULLM_BLOCK_[A-Z_]+):\?")

#: What EC2 accepts as user-data, in raw form, before it is base64-encoded. Not a round number
#: this repository chose: it is the documented ``RunInstances`` bound, and exceeding it is an
#: ``InvalidParameterValue`` on the first launch rather than anything subtler.
EC2_USER_DATA_LIMIT = 16384


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


def test_both_workflows_assume_the_role_the_template_names_for_them() -> None:
    """THE SEAM NO SINGLE FILE CAN SEE, AND THE ONE THAT FAILS ON THE SATURDAY.

    Every OIDC role in this account pins ``job_workflow_ref`` to a literal workflow path at
    ``refs/heads/main``. Renaming or moving either of these files does not fail anything here,
    in review, or at merge -- it fails at ``AssumeRole``, at the moment of the one dispatch
    that matters, with a message about a subject claim rather than about a filename.
    """
    trust = ROLE_TEMPLATE.read_text(encoding="utf-8")

    for name in (LAUNCH_FILE, RUN_FILE):
        assert f".github/workflows/{name}@refs/heads/main" in trust, name
        assert (WORKFLOWS_ROOT / name).is_file()
    assert ROLE_VARIABLE in LAUNCH_PATH.read_text(encoding="utf-8")
    assert ROLE_VARIABLE in RUN_PATH.read_text(encoding="utf-8")


def test_nothing_else_in_the_tree_assumes_the_block_fleet_role() -> None:
    """The trust names two files and this is the other half of that claim. A third workflow
    reaching for the variable is one whose token the role will refuse, which presents as a
    broken credentials step rather than as a policy that has not been widened."""
    reaching = sorted(
        path.name
        for path in WORKFLOWS_ROOT.glob("*.yml")
        if ROLE_VARIABLE in path.read_text(encoding="utf-8")
    )

    assert reaching == sorted([LAUNCH_FILE, RUN_FILE])


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
