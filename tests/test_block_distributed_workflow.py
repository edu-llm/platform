"""The dispatch surface for the run this block was bought for, and the script it sends.

``tests/test_block_workflows.py`` holds the four files that existed before this one, including
the seam that fails on the Saturday -- the OIDC trust policy enumerating workflow paths, which
this file's path had to be added to. What is here is the rest: the properties of the fifth
workflow and of the shell it sends, both of which run for the first time against a fleet that
exists for one window.

**THE SHELL IS THE PART NOBODY CAN WATCH FAIL.** It runs unattended on every node at once,
through a Systems Manager parameter, and the evidence of a mistake is eight identical
non-zero invocations at the moment the flagship run was supposed to start. So it is a file
rather than a string inside YAML or inside Python, and ``bash -n`` reads it here.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from workflow_support import (
    WORKFLOWS_ROOT,
    load_workflow,
    only_job,
    step,
    unreal_context_references,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DISTRIBUTED_PATH = WORKFLOWS_ROOT / "block-run-distributed.yml"
LAUNCH_PATH = WORKFLOWS_ROOT / "block-launch-fleet.yml"
LAUNCH_SCRIPT = PROJECT_ROOT / "infra" / "block-distributed-launch.sh"
ROLE_TEMPLATE = PROJECT_ROOT / "infra" / "iam" / "block-fleet-roles.yaml"
TOOL = PROJECT_ROOT / "tools" / "block_run_distributed.py"

REFUSAL_STEP = "Refuse a form that cannot describe a job"
CREDENTIALS_STEP = "Configure AWS credentials"
LAUNCH_STEP = "Claim the nodes and start the job"


@pytest.fixture(scope="module")
def distributed() -> dict[str, Any]:
    return load_workflow(DISTRIBUTED_PATH)


def test_starting_the_flagship_run_is_deliberately_not_limited_to_admins(
    distributed: dict[str, Any]
) -> None:
    """THE PROPERTY SOMEBODY TIDYING THIS UP WOULD BREAK FIRST, AND IT IS THE HARDEST CASE.

    ``block-launch-fleet.yml`` is admin-only because it spends money against a purchase that
    cannot be refunded, and this file starts a job on the whole fleet -- which reads as the
    larger act and is not. The machines are already up and already paid for; this spends
    nothing. Roughly fifteen of the thirty-five people here hold no AWS role at all, so an
    admin guard here hands the door back to the twenty who never needed it.

    What stops somebody taking a machine out from under a colleague is the claim on the
    machine, which is a control that works whichever door they came through.
    """
    job = only_job(distributed)
    names = [item.get("name", "") for item in job["steps"]]

    assert not any("may not make one" in name for name in names)
    assert "if" not in job


def test_the_free_refusals_happen_before_any_credential_is_assumed(
    distributed: dict[str, Any]
) -> None:
    """A run name that is not a legal directory, container name and S3 key at once fails part
    way through a clone on eight machines. A node list and a node count both filled in is a
    form where one of the two is going to be ignored. Both are answerable on a runner with no
    AWS access and no node involved."""
    names = [item.get("name", "") for item in only_job(distributed)["steps"]]
    body = step(only_job(distributed), REFUSAL_STEP)["run"]

    assert names.index(REFUSAL_STEP) < names.index(CREDENTIALS_STEP)
    assert "run_name_is_not_usable" in body
    assert "and not both and not neither" in body
    assert "git ls-remote" in body


def test_the_lane_is_serialised_against_the_launch_and_against_itself(
    distributed: dict[str, Any]
) -> None:
    """Mutation: give this its own group, or key it on the run name.

    Two distributed dispatches racing would each meet the other's half-taken claims and both
    roll back -- a correct outcome reached expensively, where a queue makes the second one meet
    a fleet that is simply busy. Sharing the group with the launch is the other half: a
    distributed run must not start into a fleet still coming up.

    ``cancel-in-progress`` would be worse than either. Cancelling between the claim phase and
    the start phase abandons claims in the one window where nothing else releases them.
    """
    launch = load_workflow(LAUNCH_PATH)

    assert distributed["concurrency"]["group"] == launch["concurrency"]["group"]
    assert distributed["concurrency"]["cancel-in-progress"] is False


def test_the_workflow_calls_the_tool_rather_than_restating_it(
    distributed: dict[str, Any]
) -> None:
    """Mutation: inline the four Systems Manager phases as shell.

    A maintainer runs exactly this from a laptop with ``--profile sbsandbox`` when GitHub is
    the thing that is broken, and the decisions inside -- whether the node set can be
    assembled, what the mesh is, what to release when a phase comes back short -- are held by
    ``tests/test_block_multinode.py`` rather than by a reviewer reading YAML once.
    """
    body = step(only_job(distributed), LAUNCH_STEP)["run"]

    assert "tools/block_run_distributed.py" in body
    assert TOOL.is_file()
    assert "--summary" in body
    assert "aws ssm" not in body


def test_the_dispatch_can_name_nodes_or_count_them_and_can_rehearse(
    distributed: dict[str, Any]
) -> None:
    """A named list is what holds node 8 back for the downstream lane; a count is what the
    ordinary case wants. ``dry_run`` is the only rehearsal this lane gets, because the fleet
    exists for one window and nothing about it can be tried twice."""
    inputs = distributed["on"]["workflow_dispatch"]["inputs"]

    assert {"nodes", "node_count", "dry_run", "expert_parallel", "fabric"} <= set(inputs)
    assert inputs["dry_run"]["default"] is False
    assert inputs["take_the_nodes_anyway"]["default"] is False
    assert inputs["fabric"]["options"] == ["auto", "efa", "tcp"]


def test_every_expression_in_the_workflow_names_something_real() -> None:
    """GitHub resolves an unknown property on a known context to the empty string rather than
    failing, so a plausible typo surfaces as an unexplained AssumeRole failure -- which for a
    reader with no AWS access is indistinguishable from the platform being broken."""
    assert unreal_context_references(DISTRIBUTED_PATH) == []


def test_the_role_already_grants_everything_this_tool_calls() -> None:
    """THE ONE THING THE NEW WORKFLOW NEEDS FROM IAM IS THE TRUST ENTRY AND NOTHING ELSE.

    ``tests/test_block_workflows.py`` holds the trust list, which had to grow by this file's
    path. What this holds is the other direction: the four Systems Manager actions and the one
    EC2 read this tool makes are already granted, so re-applying the stack is a trust change
    rather than a widening of what the role can do.
    """
    template = yaml.safe_load(ROLE_TEMPLATE.read_text(encoding="utf-8"))
    statements = template["Resources"]["BlockFleetRole"]["Properties"]["Policies"][0][
        "PolicyDocument"
    ]["Statement"]
    granted = {
        action
        for statement in statements
        if statement["Effect"] == "Allow"
        for action in (
            statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]]
        )
    }

    assert granted >= {
        "ec2:DescribeInstances",
        "ssm:SendCommand",
        "ssm:ListCommandInvocations",
        "ssm:GetCommandInvocation",
    }


# ---------------------------------------------------------------------------------------
# The script the tool sends.
# ---------------------------------------------------------------------------------------


def test_the_launch_script_is_a_file_rather_than_a_string_in_either_caller() -> None:
    """Inlined, it stops being something ``bash -n`` and ``shellcheck`` read and becomes a
    two-hundred-line quoted blob inside YAML or inside Python -- and it is the one artifact in
    this path that nobody can watch fail."""
    body = DISTRIBUTED_PATH.read_text(encoding="utf-8")

    tool = TOOL.read_text(encoding="utf-8")

    assert LAUNCH_SCRIPT.is_file()
    for caller in (body, tool):
        assert "--network host" not in caller
        assert "--gpus all" not in caller
    assert "LAUNCH_SCRIPT.read_text(" in tool


def test_the_container_joins_the_host_network_because_the_rendezvous_is_dialled_from_outside() -> (
    None
):
    """Mutation: keep the single-node path's default bridge network.

    The c10d store binds a listener on the elected node and every other node dials it at that
    machine's private address. On the bridge network the listener lives in a namespace nothing
    outside the machine can reach, and what that looks like is seven nodes timing out against a
    store that is running perfectly.
    """
    script = LAUNCH_SCRIPT.read_text(encoding="utf-8")

    assert "--network host" in script
    assert "--ipc=host" in script
    assert "--ulimit memlock=-1" in script


def test_the_script_never_decides_a_rank_and_never_names_a_master() -> None:
    """Every rank decision belongs to the rendezvous. A script that computed a node index --
    from the node tag, from a position in a list, from anything -- would be computing something
    torchrun ignores, and the two would disagree silently rather than loudly."""
    script = LAUNCH_SCRIPT.read_text(encoding="utf-8")

    assert "NODE_RANK" not in script
    assert "MASTER_ADDR" not in script
    assert "MASTER_PORT" not in script
    assert "--node-rank" not in script


def test_the_fabric_is_asked_of_the_device_and_never_of_the_instance_type() -> None:
    """A ``p5.48xlarge`` can carry thirty-two EFA interfaces and carries none unless
    ``run-instances`` asked for them by ``InterfaceType=efa``. So a launch that did not ask
    produces a machine with the driver installed, the plugin installed, 3,200 Gbps of
    advertised fabric and no ``/dev/infiniband`` -- and a script keyed on the shape would
    configure NCCL for a fabric that is not there."""
    script = LAUNCH_SCRIPT.read_text(encoding="utf-8")

    assert "/dev/infiniband/uverbs0" in script
    assert "FI_PROVIDER=efa" in script
    assert "/opt/amazon:/opt/amazon:ro" in script
    assert "NCCL_SOCKET_IFNAME" in script
    assert "NCCL_DEBUG=INFO" in script


def test_asking_for_the_fabric_and_not_getting_it_is_a_refusal() -> None:
    """``auto`` is the setting a fleet launched without EFA needs and it falls back quietly by
    design. ``efa`` is somebody asking for a guarantee, and a job that silently ran several
    times slower would be the worst possible answer to that question."""
    script = LAUNCH_SCRIPT.read_text(encoding="utf-8")

    assert 'FABRIC_MODE}" = efa' in script
    assert "has no EFA device" in script


def test_the_deprecated_efa_settings_are_deliberately_absent() -> None:
    """``FI_EFA_USE_DEVICE_RDMA=1`` and ``NCCL_PROTO=simple`` are AWS's advice for
    ``aws-ofi-nccl`` at or below 1.6. The AMI carries 1.17 or later, where the first is a
    no-op and the second caps the protocol NCCL would otherwise choose better. Both are the
    kind of line somebody copies from a blog post into a file like this."""
    script = LAUNCH_SCRIPT.read_text(encoding="utf-8")
    settings = [line for line in script.splitlines() if "--env" in line]

    assert not any("FI_EFA_USE_DEVICE_RDMA" in line for line in settings)
    assert not any("NCCL_PROTO" in line for line in settings)


def test_every_rank_writes_its_checkpoints_to_one_prefix() -> None:
    """Mutation: use each machine's own node prefix, which is what the single-node path does.

    A distributed checkpoint is one directory written by every rank together. Per-node prefixes
    produce as many partial saves as there are machines and no whole one, and the failure is
    invisible until somebody tries to read one back after the window.
    """
    script = LAUNCH_SCRIPT.read_text(encoding="utf-8")
    prefix = re.search(r"^readonly OUTPUT_PREFIX=.*$", script, re.MULTILINE)

    assert prefix is not None
    assert "${OUTPUT_NODE}" in prefix.group()
    assert "${EDULLM_BLOCK_NODE}" not in prefix.group()
    assert 'EDULLM_CHECKPOINT_DIR=${OUTPUT_PREFIX}/checkpoints/' in script


def test_the_log_lands_where_the_sync_unit_and_the_drain_already_look() -> None:
    """The whole reason this path does not need its own reporting: the container is named like
    every other, the log is at ``/scratch/<run>/log/train.log`` where the sync unit walks, and
    the tree is under ``/scratch`` where the drain timer walks. ``edullm-node status``,
    ``tools/block_status.py``, ``block-drain.yml`` and ``block-logs.yml`` all see a distributed
    run without knowing there is such a thing."""
    script = LAUNCH_SCRIPT.read_text(encoding="utf-8")

    assert 'TREE="${EDULLM_BLOCK_SCRATCH}/${RUN_NAME}"' in script
    assert '--name "edullm-${RUN_NAME}"' in script
    assert "/work/log/train.log" in script


@pytest.mark.slow
def test_the_launch_script_parses_as_bash() -> None:
    """It is sent through a Systems Manager parameter to every node at once and nobody watches
    it run. An unbalanced quote is eight identical failures at 06:35 on the Saturday."""
    checked = subprocess.run(
        ["bash", "-n", str(LAUNCH_SCRIPT)], check=False, capture_output=True, text=True
    )

    assert checked.returncode == 0, checked.stderr
