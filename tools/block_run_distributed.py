"""Start one training job across several capacity block nodes, or start nothing at all.

``.github/workflows/block-run.yml`` hands out one node and is right for the weekend's ordinary
work. It cannot start the run the block was bought for, which is one job on every node at once,
and the difference is not a bigger number: eight machines have to be taken together or not at
all, they have to agree where to meet, and a failure on one of them has to end the other seven
rather than leave them holding cards nobody can use.

**THE ORDER OF THE PHASES IS THE WHOLE DESIGN.** Everything that can be refused for free is
refused before a claim is written -- the node set, the card count, the mesh, the command --
and ``edullm_platform.block_multinode.plan_launch`` does all of it in one pass off two API
reads. Then the claims are taken in one call, and only then is anything cloned or started. The
state this cannot end in is five machines locked for a run that never started, which is not
hypothetical: it is what a loop over ``edullm-node run`` produces the first time somebody else
is on node six.

**A ROLLBACK IS NOT AN ERROR PATH HERE, IT IS THE OTHER HALF OF THE CLAIM.** A claim phase that
comes back short releases what it took; a start phase that comes back short removes the
containers that did start and releases everything. Both run before the tool reports, and both
report what they did, because a rollback that quietly half worked is worse than none.

**WHAT IT DOES NOT DO IS SCHEDULE.** There is no queue and no retry. A dispatch either gets the
whole set or says which machines it could not have and why, and the answer to the second is a
person -- which is the answer the rest of this lane gives too.
"""

from __future__ import annotations

import argparse
import base64
import json
import shlex
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from edullm_platform.block_fleet import (
    NODE_TAG,
    REMOTE_READING_SCRIPT,
    RESERVATION_TAG,
    FleetNode,
    NodeReading,
    parse_reading,
    read_fleet,
)
from edullm_platform.block_multinode import (
    DEFAULT_RENDEZVOUS_PORT,
    ROUTED_EXPERTS,
    Candidate,
    LaunchPlan,
    NodeOutcome,
    launch_markdown,
    outcomes,
    plan_launch,
    refused,
)
from edullm_platform.capture_tooling import CaptureFailedError, aws_json

__all__ = ["build_parser", "launch", "main", "node_settings", "prelude"]

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]

#: The node-side half of this tool. Read off disk and sent rather than written inline, for the
#: reason ``infra/block-node-bootstrap.sh`` is a file rather than a heredoc: it is the part
#: nobody can watch fail, so it wants to be the most readable artifact in the change and one
#: that ``bash -n`` and ``shellcheck`` can each take on their own.
LAUNCH_SCRIPT: Final = PROJECT_ROOT / "infra" / "block-distributed-launch.sh"

#: What Systems Manager calls a finished invocation, whichever way it finished. Restated rather
#: than imported from the sibling tools, which is the convention this directory already follows
#: about tools importing one another.
TERMINAL_STATUSES: Final = frozenset(
    {"Success", "Failed", "Cancelled", "TimedOut", "Undeliverable", "Terminated"}
)

#: How long a node may take to read two counters and a small file.
PROBE_SECONDS: Final = 30

#: How long a node may take to take or give back a claim, which is one comparison and one write.
CLAIM_SECONDS: Final = 120

#: How long the start phase may take on a node. It clones a branch, asks the image whether the
#: launcher is on its PATH, and starts a detached container -- so the slow part is the clone
#: rather than the training. ``docker run --detach`` returns as soon as the container is up,
#: which is what lets a job spanning eight machines be launched by something that finishes in
#: a couple of minutes.
START_SECONDS: Final = 900

#: How long a command may sit undelivered before Systems Manager abandons it. Delivery rather
#: than execution: these are two different timeouts and confusing them kills a clone part way
#: through and reports it as a node that failed.
DELIVERY_SECONDS: Final = 600

POLL_SECONDS: Final = 5


def _fleet(
    *, reservation_id: str | None, profile: str | None, region: str
) -> tuple[FleetNode, ...]:
    """Every running node, from EC2 rather than from Systems Manager.

    EC2 is the denominator here for the reason ``tools/block_status.py`` gives: a machine that
    is running and produced no invocation is a finding, and a reader that started from the
    invocation list has nothing to notice.
    """
    filters = [
        f"Name=tag-key,Values={NODE_TAG}",
        "Name=instance-state-name,Values=running",
    ]
    if reservation_id is not None:
        filters.append(f"Name=tag:{RESERVATION_TAG},Values={reservation_id}")
    return read_fleet(
        aws_json(
            ["ec2", "describe-instances", "--filters", *filters], profile=profile, region=region
        )
    )


def _send(
    *,
    instance_ids: Sequence[str],
    command: str,
    comment: str,
    execution_seconds: int,
    profile: str | None,
    region: str,
) -> str:
    """One command to a named set of machines, addressed by id rather than by tag.

    **BY ID AND NOT BY TAG, WHICH IS THE OPPOSITE OF WHAT THE READING TOOLS DO.** They target a
    tag because an unregistered agent would otherwise cost them the reading of the whole fleet,
    and a fleet-wide reading that quietly skips a machine is the thing they exist to avoid.
    This is the other case. The set is decided, a machine missing from it is a job that cannot
    form, and ``InvalidInstanceId`` on the way in is a better failure than a launch that starts
    on seven of the eight nodes it was told to use and waits forever for the eighth.
    """
    answer = aws_json(
        [
            "ssm",
            "send-command",
            "--document-name",
            "AWS-RunShellScript",
            "--comment",
            comment,
            "--instance-ids",
            *instance_ids,
            "--timeout-seconds",
            str(DELIVERY_SECONDS),
            "--parameters",
            json.dumps({"commands": [command], "executionTimeout": [str(execution_seconds)]}),
        ],
        profile=profile,
        region=region,
    )
    command_id = (answer.get("Command") or {}).get("CommandId")
    if not isinstance(command_id, str) or not command_id:
        raise CaptureFailedError("send_command_returned_no_command_id")
    return command_id


def _settle(
    *, command_id: str, expected: int, wait_seconds: int, profile: str | None, region: str
) -> dict[str, Mapping[str, Any]]:
    """Every invocation of one command, once they have all finished or the wait runs out.

    The list call is polled and ``get-command-invocation`` is what is finally read, which looks
    like one call too many and is not. ``list-command-invocations`` without ``--details`` says
    nothing about output, and with it truncates the output at a couple of thousand characters
    -- and the output of the start phase is what says which fabric each node chose. One
    detailed read per node, at the end, once.
    """
    deadline = time.monotonic() + wait_seconds
    seen: dict[str, Mapping[str, Any]] = {}
    while True:
        listed = aws_json(
            ["ssm", "list-command-invocations", "--command-id", command_id],
            profile=profile,
            region=region,
        )
        seen = {
            str(invocation["InstanceId"]): invocation
            for invocation in listed.get("CommandInvocations") or []
            if isinstance(invocation, Mapping) and invocation.get("InstanceId")
        }
        settled = sum(
            1 for found in seen.values() if str(found.get("Status")) in TERMINAL_STATUSES
        )
        if (settled >= expected and len(seen) >= expected) or time.monotonic() >= deadline:
            break
        time.sleep(POLL_SECONDS)

    return {
        instance_id: aws_json(
            [
                "ssm",
                "get-command-invocation",
                "--command-id",
                command_id,
                "--instance-id",
                instance_id,
            ],
            profile=profile,
            region=region,
        )
        for instance_id in seen
    }


def _read_fleet_state(
    *, fleet: Sequence[FleetNode], profile: str | None, region: str, wait_seconds: int
) -> tuple[NodeReading, ...]:
    """What every node is doing, through the reader the status tool already uses.

    The shared snippet rather than ``edullm-node status``, for the reason
    ``block_fleet.REMOTE_READING_SCRIPT`` gives: the node most worth asking about is the one
    whose bootstrap did not finish, and on that node the helper was never installed.
    """
    if not fleet:
        return ()
    command_id = _send(
        instance_ids=[node.instance_id for node in fleet],
        command=REMOTE_READING_SCRIPT,
        comment="edullm block distributed probe",
        execution_seconds=PROBE_SECONDS,
        profile=profile,
        region=region,
    )
    answers = _settle(
        command_id=command_id,
        expected=len(fleet),
        wait_seconds=wait_seconds,
        profile=profile,
        region=region,
    )
    return tuple(
        parse_reading(
            node=node.node,
            instance_id=node.instance_id,
            status=str((answers.get(node.instance_id) or {}).get("Status") or "no invocation"),
            output=str((answers.get(node.instance_id) or {}).get("StandardOutputContent") or ""),
        )
        for node in fleet
    )


def prelude(settings: Mapping[str, str]) -> str:
    """The ``NAME=value`` lines the launch script reads, in the shape it reads them.

    ``shlex.quote`` on every value rather than on the ones that look dangerous. What goes
    through here already includes a base64 blob and a branch name, and quoting all of them is
    what makes the next value somebody adds safe without them having to think about it.

    **THE FIRST LINE IS A SHEBANG AND WITHOUT IT NONE OF THE REST RUNS.** Systems Manager
    writes an ``AWS-RunShellScript`` payload to a file and executes it, honouring a ``#!`` on
    line one and falling back to ``/bin/sh`` -- which is ``dash`` on this AMI family -- when
    there is not one. ``infra/block-distributed-launch.sh`` carries its own shebang, and that
    shebang is not on line one of what is sent: the settings below go in front of it, exactly
    as ``.github/workflows/block-launch-fleet.yml`` puts them in front of the bootstrap. So
    the payload arrives at ``dash``, and ``dash`` refuses ``set -o pipefail`` on the script's
    thirty-second line, before a claim is rewritten or anything is cloned. Every node fails
    identically with ``Illegal option -o pipefail`` and no other output. The workflow prints
    ``#!/bin/bash`` ahead of the bootstrap's settings for this reason; this is the same line
    for the same reason.
    """
    return "\n".join(
        (
            "#!/bin/bash",
            *(f"{name}={shlex.quote(value)}" for name, value in sorted(settings.items())),
        )
    )


def node_settings(
    *,
    plan: LaunchPlan,
    who: str,
    repository: str,
    branch: str,
    wandb_project: str,
    fabric: str,
) -> dict[str, str]:
    """Everything the launch script refuses to start without, and nothing per-node.

    **NOT ONE OF THESE VALUES DIFFERS BETWEEN MACHINES, WHICH IS THE POINT RATHER THAN A
    COINCIDENCE.** The rendezvous form of torchrun works the ranks out for itself, so there is
    no rank, no node index and no master flag to get wrong per node -- and because there is
    nothing per node, the launch is one Systems Manager call to the whole set rather than one
    per machine with a loop that can fail half way through.

    ``EDULLM_DIST_OUTPUT_NODE`` is a node number rather than an S3 URI, and that is the one
    entry here worth arguing for. A distributed checkpoint is one directory written by every
    rank together, so all of them have to name the same prefix -- and assembling that prefix
    here would mean this tool holding a copy of the bucket name and the reservation id that
    ``/etc/edullm-block.env`` already holds on every machine. The number says whose prefix, the
    node builds it out of what the bootstrap wrote, and there is no second copy to drift.
    """
    assert plan.rendezvous is not None  # plan.usable is the caller's precondition
    return {
        "EDULLM_DIST_RUN": plan.rendezvous.run_id,
        "EDULLM_DIST_WHO": who,
        "EDULLM_DIST_REPOSITORY": repository,
        "EDULLM_DIST_BRANCH": branch,
        "EDULLM_DIST_LAUNCH_BASE64": base64.b64encode(
            plan.launch_command.encode("utf-8")
        ).decode("ascii"),
        "EDULLM_DIST_OUTPUT_NODE": str(plan.rendezvous.host.node),
        "EDULLM_DIST_WANDB_PROJECT": wandb_project,
        "EDULLM_DIST_RENDEZVOUS_HOST": plan.rendezvous.endpoint,
        "EDULLM_DIST_WORLD_SIZE": str(plan.mesh.world_size),
        "EDULLM_DIST_FABRIC": fabric,
    }


def _release(
    *,
    chosen: Sequence[Candidate],
    run: str,
    remove_containers: bool,
    profile: str | None,
    region: str,
) -> tuple[NodeOutcome, ...]:
    """Give the claims back, and take down anything that started.

    **IT REMOVES CONTAINERS RATHER THAN ASKING THEM TO STOP, AND THAT IS THE OPPOSITE OF WHAT
    THE DRAIN DOES.** ``edullm-node drain --stop-runs`` signals the trainer so that OLMo-core
    saves a final checkpoint, which is right at the end of a window and means nothing here:
    what is being removed is seconds old, has written no checkpoint, and is sitting at a
    rendezvous that is never going to complete. Waiting politely for it costs the claims.

    **IT GIVES BACK ONLY A CLAIM THAT NAMES THIS RUN**, which matters in the two cases where
    the claim on a node is not this dispatch's. ``--force`` skips the claim phase entirely, so
    a forced launch that then fails would otherwise clear the claim of the person whose machine
    it took -- ending their lock while their container keeps its cards, which is the one state
    worse than either. The other case is a race: somebody claims a node in the seconds between
    a failed start and this rollback.

    ``|| true`` on each part so that a node where nothing started still releases and a node
    that cannot release still loses its container. A rollback that stops at its first failure
    leaves exactly the mess it was called to clear.
    """
    quoted = shlex.quote(run)
    parts = [
        # The same one-field read `edullm-node` does, and for its reason: `jq` is not on every
        # image this AMI family has shipped, and the claim is one flat object with a validated
        # character set, so the grammar this has to cover is one expression wide.
        (
            "held=$(sed -n 's/.*\"run\":\"\\([^\"]*\\)\".*/\\1/p' /var/lib/edullm/claim.json"
            " 2>/dev/null || true)"
        ),
    ]
    if remove_containers:
        parts.append(f"docker rm --force {shlex.quote(f'edullm-{run}')} > /dev/null 2>&1 || true")
    parts.append(f'[ "$held" = {quoted} ] && edullm-node release --force || true')
    command_id = _send(
        instance_ids=[candidate.instance_id for candidate in chosen],
        command="; ".join(parts),
        comment=f"edullm block distributed rollback {run}",
        execution_seconds=CLAIM_SECONDS,
        profile=profile,
        region=region,
    )
    return outcomes(
        chosen,
        _settle(
            command_id=command_id,
            expected=len(chosen),
            wait_seconds=CLAIM_SECONDS,
            profile=profile,
            region=region,
        ),
    )


def _fabric_of(found: Sequence[NodeOutcome]) -> dict[int, str]:
    """Which fabric each node chose, out of the tab-separated record the launch script prints."""
    chosen: dict[int, str] = {}
    for outcome in found:
        if outcome.node is None:
            continue
        for line in outcome.output.splitlines():
            key, separator, value = line.partition("\t")
            if separator and key.strip() == "fabric":
                chosen[outcome.node] = value.strip()
    return chosen


def _reservation_of(*, profile: str | None, region: str) -> str | None:
    """Which block has a fleet up, read off the instances rather than typed on the form.

    Only the report needs this -- the nodes build their own S3 prefixes out of what the
    bootstrap wrote into ``/etc/edullm-block.env`` -- so it is a convenience rather than a
    control, and it answers ``None`` rather than refusing when there is nothing tagged. Two
    live blocks is the case it will not guess at, for the reason ``tools/block_drain.py``
    gives: node numbers repeat across fleets.
    """
    described = aws_json(
        [
            "ec2",
            "describe-instances",
            "--filters",
            f"Name=tag-key,Values={RESERVATION_TAG}",
            "Name=instance-state-name,Values=running",
            "--query",
            f"Reservations[].Instances[].Tags[?Key=='{RESERVATION_TAG}'].Value",
        ],
        profile=profile,
        region=region,
    )
    found: set[str] = set()
    for group in described or []:
        for value in group if isinstance(group, list) else [group]:
            if isinstance(value, str) and value:
                found.add(value)
    if len(found) > 1:
        raise CaptureFailedError(f"more_than_one_block_has_a_fleet_up:{','.join(sorted(found))}")
    return next(iter(found), None)


@dataclass(frozen=True)
class Launched:
    """What one dispatch did, whether or not it started anything."""

    code: int
    plan: LaunchPlan
    fabric: dict[int, str]
    reservation_id: str


def _report_failure(phase: str, found: Sequence[NodeOutcome]) -> None:
    for outcome in refused(found):
        detail = (outcome.error or outcome.output).strip().splitlines()
        print(
            f"{phase}_failed_on_node_{outcome.node}:{outcome.instance_id} {outcome.status} "
            f"{detail[-1] if detail else ''}",
            file=sys.stderr,
        )


def launch(arguments: argparse.Namespace) -> Launched:
    """Read, plan, claim, start -- and undo the claim if either of the last two came back short.

    The plan comes back beside the exit code so that ``main`` prints the same report whether
    the launch happened or was refused. A refusal a person cannot see the plan behind is a
    refusal they cannot act on.
    """
    reservation_id = arguments.reservation or _reservation_of(
        profile=arguments.profile, region=arguments.region
    )
    fleet = _fleet(
        reservation_id=arguments.reservation,
        profile=arguments.profile,
        region=arguments.region,
    )
    if not fleet:
        print("no running instance carries an edullm:node tag in this region", file=sys.stderr)
        raise CaptureFailedError("no_fleet_is_up")

    readings = _read_fleet_state(
        fleet=fleet,
        profile=arguments.profile,
        region=arguments.region,
        wait_seconds=arguments.wait_seconds,
    )
    plan = plan_launch(
        fleet=fleet,
        readings=readings,
        run=arguments.run,
        command=arguments.command,
        requested=arguments.nodes,
        node_count=arguments.node_count,
        expert_parallel=arguments.expert_parallel,
        routed_experts=arguments.routed_experts,
        port=arguments.rendezvous_port,
        max_restarts=arguments.max_restarts,
        join_timeout_seconds=arguments.join_timeout_seconds,
        mesh_flags=arguments.mesh_flags,
        force=arguments.force,
    )
    if not plan.usable:
        for refusal in plan.refusals:
            print(f"distributed_launch_refused:{refusal}", file=sys.stderr)
        return Launched(code=1, plan=plan, fabric={}, reservation_id=reservation_id or "")
    assert plan.rendezvous is not None

    if arguments.dry_run:
        return Launched(code=0, plan=plan, fabric={}, reservation_id=reservation_id or "")

    # THE CLAIM PHASE GOES THROUGH THE HELPER RATHER THAN WRITING THE FILE, SO THAT THERE IS
    # ONE DEFINITION OF WHAT A CLAIM REFUSES. `edullm-node claim` is what a person in a shell
    # runs and what refuses a node somebody else holds, and a second implementation of that
    # comparison here would be a second thing to keep in step with the single-node path.
    #
    # `--force` skips this phase entirely rather than passing a flag through it: the helper has
    # no way to override a claim, and the launch script's own write is what takes the node.
    # That is the documented consequence of forcing -- the other run keeps its cards and the
    # two fight for memory -- and it is why the phase is skipped rather than softened.
    if not arguments.force:
        claim = f"edullm-node claim {shlex.quote(arguments.run)} {shlex.quote(arguments.who)}"
        command_id = _send(
            instance_ids=[candidate.instance_id for candidate in plan.chosen],
            command=claim,
            comment=f"edullm block distributed claim {arguments.run}",
            execution_seconds=CLAIM_SECONDS,
            profile=arguments.profile,
            region=arguments.region,
        )
        claimed = outcomes(
            plan.chosen,
            _settle(
                command_id=command_id,
                expected=len(plan.chosen),
                wait_seconds=CLAIM_SECONDS,
                profile=arguments.profile,
                region=arguments.region,
            ),
        )
        if refused(claimed):
            _report_failure("claim", claimed)
            granted = tuple(
                candidate
                for candidate, outcome in zip(plan.chosen, claimed, strict=True)
                if outcome.ok
            )
            if granted:
                print(
                    f"releasing the {len(granted)} nodes that did grant the claim, so that a "
                    "partial reservation does not survive a refused launch",
                    file=sys.stderr,
                )
                _release(
                    chosen=granted,
                    run=arguments.run,
                    remove_containers=False,
                    profile=arguments.profile,
                    region=arguments.region,
                )
            return Launched(code=1, plan=plan, fabric={}, reservation_id=reservation_id or "")

    settings = node_settings(
        plan=plan,
        who=arguments.who,
        repository=arguments.repository,
        branch=arguments.branch,
        wandb_project=arguments.wandb_project,
        fabric=arguments.fabric,
    )
    command_id = _send(
        instance_ids=[candidate.instance_id for candidate in plan.chosen],
        command=f"{prelude(settings)}\n{LAUNCH_SCRIPT.read_text(encoding='utf-8')}",
        comment=f"edullm block distributed start {arguments.run}",
        execution_seconds=START_SECONDS,
        profile=arguments.profile,
        region=arguments.region,
    )
    started = outcomes(
        plan.chosen,
        _settle(
            command_id=command_id,
            expected=len(plan.chosen),
            wait_seconds=arguments.start_wait_seconds,
            profile=arguments.profile,
            region=arguments.region,
        ),
    )
    if refused(started):
        _report_failure("start", started)
        print(
            "removing every container this launch started and releasing every claim it took. "
            "A job that cannot form does not sit at a rendezvous holding the fleet.",
            file=sys.stderr,
        )
        _release(
            chosen=plan.chosen,
            run=arguments.run,
            remove_containers=True,
            profile=arguments.profile,
            region=arguments.region,
        )
        return Launched(
            code=1,
            plan=plan,
            fabric=_fabric_of(started),
            reservation_id=reservation_id or "",
        )

    return Launched(
        code=0, plan=plan, fabric=_fabric_of(started), reservation_id=reservation_id or ""
    )


def _node_list(value: str) -> tuple[int, ...]:
    if not value.strip():
        return ()
    try:
        return tuple(int(part) for part in value.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"a node list is comma-separated numbers, got {value!r}"
        ) from None


def build_parser() -> argparse.ArgumentParser:
    """Named so ``tests/test_workflow_tool_arguments.py`` can import and read it."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--run", required=True, help="names the claim, the container and the log")
    parser.add_argument("--branch", required=True, help="the branch every node clones")
    parser.add_argument(
        "--command",
        required=True,
        help=(
            "the training entrypoint and its arguments, with no launcher. The rendezvous flags "
            "are decided here because they depend on which nodes were claimed. The first word "
            "is exec'd, so it names a program -- `python <script>` rather than `<script>`, and "
            "no VAR=value in front of it"
        ),
    )
    parser.add_argument("--repository", default="edu-llm/OLMo-core")
    parser.add_argument("--who", default="unknown", help="who the claim on each node names")
    parser.add_argument(
        "--nodes",
        type=_node_list,
        default=(),
        help="which nodes, comma-separated. Mutually exclusive with --node-count",
    )
    parser.add_argument(
        "--node-count",
        type=int,
        default=None,
        help="how many nodes, taking the lowest-numbered free ones",
    )
    parser.add_argument(
        "--expert-parallel",
        type=int,
        default=None,
        help=(
            "the MoE expert-parallel degree, which is also the HSDP shard degree. Left off, the "
            "widest one that still fits inside a single machine is used, which keeps the "
            "all-to-all on NVLink"
        ),
    )
    parser.add_argument("--routed-experts", type=int, default=ROUTED_EXPERTS)
    parser.add_argument(
        "--no-mesh-flags",
        dest="mesh_flags",
        action="store_false",
        help="do not add or check the MoE mesh flags, for a command that is not that recipe",
    )
    parser.add_argument("--rendezvous-port", type=int, default=DEFAULT_RENDEZVOUS_PORT)
    parser.add_argument(
        "--max-restarts",
        type=int,
        default=0,
        help=(
            "how many times torchrun may re-form the group after a rank dies. Zero is the "
            "whole of how this job fails as a unit"
        ),
    )
    parser.add_argument("--join-timeout-seconds", type=int, default=900)
    parser.add_argument(
        "--fabric",
        choices=("auto", "efa", "tcp"),
        default="auto",
        help=(
            "auto takes EFA where the devices are there and TCP where they are not; efa "
            "refuses a node that has none"
        ),
    )
    parser.add_argument("--reservation", default=None)
    parser.add_argument("--region", default="us-east-2")
    parser.add_argument("--bucket", default="edullm-block-outputs-us-east-2")
    parser.add_argument("--wandb-project", default="capacity-block")
    parser.add_argument("--wandb-entity", default="eduLLM")
    # A laptop is a first-class caller here, which is why this defaults rather than being None.
    # `--no-profile` is what a workflow runner passes, holding ambient credentials from a role
    # it has already assumed.
    parser.add_argument("--profile", default="sbsandbox")
    parser.add_argument(
        "--no-profile",
        dest="profile",
        action="store_const",
        const=None,
        help="use the ambient credentials, which is what a workflow runner has",
    )
    parser.add_argument("--wait-seconds", type=int, default=60)
    parser.add_argument("--start-wait-seconds", type=int, default=START_SECONDS)
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "start even on nodes somebody else holds. Their run keeps its cards and yours will "
            "fight it for memory; ask them first"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="read the fleet and print the plan, claiming nothing and starting nothing",
    )
    parser.add_argument("--summary", default=None, help="append the report to GITHUB_STEP_SUMMARY")
    parser.add_argument("--json", action="store_true")
    return parser


def _as_json(plan: LaunchPlan, *, fabric: Mapping[int, str]) -> str:
    return json.dumps(
        {
            "nodes": [
                {
                    "node": candidate.node,
                    "instance_id": candidate.instance_id,
                    "private_ip": candidate.private_ip,
                    "fabric": fabric.get(candidate.node),
                }
                for candidate in plan.chosen
            ],
            "world_size": plan.mesh.world_size,
            "expert_parallel": plan.mesh.expert_parallel,
            "replicas": plan.mesh.replicas,
            "all_to_all_crosses_the_fabric": plan.mesh.all_to_all_crosses_the_fabric,
            "rendezvous": plan.rendezvous.endpoint if plan.rendezvous is not None else None,
            "command": plan.launch_command,
            "refusals": list(plan.refusals),
        },
        indent=2,
        sort_keys=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        done = launch(arguments)
    except CaptureFailedError as error:
        print(error.reason, file=sys.stderr)
        return 2

    plan = done.plan
    if arguments.json:
        print(_as_json(plan, fabric=done.fabric))
    elif plan.rendezvous is not None:
        print(plan.mesh.describe())
        print(f"rendezvous {plan.rendezvous.endpoint} on node {plan.rendezvous.host.node}")
        print(plan.launch_command)

    if arguments.summary and plan.rendezvous is not None:
        with Path(arguments.summary).open("a", encoding="utf-8") as page:
            page.write(
                launch_markdown(
                    run=arguments.run,
                    mesh=plan.mesh,
                    rendezvous=plan.rendezvous,
                    chosen=plan.chosen,
                    fabric=done.fabric,
                    command=plan.launch_command,
                    bucket=arguments.bucket,
                    reservation_id=done.reservation_id,
                    region=arguments.region,
                    entity=arguments.wandb_entity,
                    project=arguments.wandb_project,
                )
                + "\n"
            )
    return done.code


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
