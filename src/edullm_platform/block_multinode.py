"""One job across several capacity block nodes: which machines, which mesh, and the launcher.

``src/edullm_platform/block_fleet.py`` reads a fleet and ``block-run.yml`` hands out one node
at a time. Neither of them can start the run this block was bought for, which is one job on
every node at once, and the three decisions that job needs are the three this module holds.

**AN ALL-OR-NOTHING CLAIM IS THE PART THAT IS NEW, AND THE FAILURE IT AVOIDS IS SPECIFIC.**
The single-node path takes one claim and either gets it or does not. Across eight nodes the
interesting outcome is neither: five claims taken, three refused, nothing running, and a fleet
that reads as fully occupied to everybody else in the window. :func:`choose_nodes` decides the
whole set before anything is written, and it names *every* blocker rather than the first one --
a person who is told node 3 is busy will fix node 3, dispatch again, and be told about node 6.

**THE MESH ARITHMETIC IS HERE BECAUSE ITS WRONG ANSWERS ARE QUIET.** ``--moe-shard-degree``
and ``--moe-num-replicas`` on ``edullm/final-model`` are the expert-parallel degree and the
HSDP replica count, and OLMo-core refuses only the pairs that cannot describe the world size
at all. The pair that costs the window is one it accepts: an expert-parallel degree wider than
a node puts the MoE all-to-all on the fabric between machines instead of on the NVLink inside
one, and nothing about that reads as wrong -- the run starts, the loss falls, and every step
takes several times what it should. :func:`node_local_expert_parallel` is the default for that
reason, and :func:`mesh_refusals` transcribes OLMo-core's own rules so a mesh that its
validator would reject is refused here, before a claim is taken.

**THE LAUNCHER IS THE RENDEZVOUS FORM AND NEVER THE STATIC ONE.** ``torchrun`` has two ways to
tell a process which rank it is. The static form wants ``--node-rank`` different on every
machine; the rendezvous form wants ``--rdzv-backend`` and ``--rdzv-endpoint`` identical on
every machine and works the rank out itself. Mixing them is the trap: ``--node-rank`` is
ignored once a rendezvous backend is set, so a command carrying both looks like it assigns
ranks and does not, and what comes out is a job that rendezvouses into an order nobody chose.
:func:`torchrun_command` builds the rendezvous form only, and the same string goes to every
node -- which is also what makes the launch one Systems Manager call rather than one per node.

**AND THE POSITIONAL AFTER THOSE FLAGS IS A PROGRAM RATHER THAN A SCRIPT, WHICH IS WHAT
``--no-python`` BUYS.** torchrun's positional is a script path and torchrun supplies the
interpreter itself, so a command line appended to the flags unchanged becomes ``python -u
python .edullm/train_on_corpus.py --flags`` and every rank opens a file named ``python``. What
this lane carries is a command line -- ``.edullm/run.yaml`` holds one and ``edullm-node run``
hands the identical string to ``bash -lc`` -- so the positional is made a program instead.
:func:`command_refusals` refuses the two shapes that are not one.
"""

from __future__ import annotations

import shlex
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from edullm_platform.block_fleet import FleetNode, NodeReading

__all__ = [
    "DEFAULT_RENDEZVOUS_PORT",
    "MESH_FLAGS",
    "ROUTED_EXPERTS",
    "Candidate",
    "ExpertMesh",
    "LaunchPlan",
    "NodeChoice",
    "NodeOutcome",
    "Rendezvous",
    "blocker_for",
    "cards_per_node",
    "choose_nodes",
    "command_refusals",
    "expert_parallel_choices",
    "launch_markdown",
    "mesh_for",
    "mesh_refusals",
    "node_local_expert_parallel",
    "outcomes",
    "plan_launch",
    "refused",
    "rendezvous_for",
    "run_name_refusals",
    "torchrun_command",
    "with_mesh_flags",
]

#: How many routed experts the model this block was bought for has, which is the number an
#: expert-parallel degree has to divide. It is a property of the OLMo-core recipe --
#: ``olmoe_7b_32x4`` in ``.edullm/train_on_corpus.py`` on ``edullm/final-model`` -- rather than
#: of this platform, so every function below takes it as an argument and this is only the
#: default. A different MoE is a different number and not a different code path.
ROUTED_EXPERTS: Final = 32

#: The port the c10d rendezvous is hosted on, which is torchrun's own default and is left at it
#: deliberately. Nothing in this lane picks a port anywhere else, the nodes share the VPC's
#: default security group and that group admits every port from its own members, so a different
#: number would buy nothing and would be one more thing to get wrong between two files.
DEFAULT_RENDEZVOUS_PORT: Final = 29400

#: The two flags on ``edullm/final-model`` that say what the mesh is, in the order a person
#: reads them. Named here so that :func:`with_mesh_flags` can check a command against the mesh
#: this module computed rather than trusting that somebody typed the pair that matches the
#: number of nodes they asked for -- which is the one mistake in this file that produces a run
#: that works and is slow, instead of a run that refuses.
MESH_FLAGS: Final = ("--moe-shard-degree", "--moe-num-replicas")

#: What a launcher already in the command looks like. A command carrying one of these is about
#: to be wrapped in a second one, which starts eight processes per node each of which starts
#: eight more, and the first evidence is sixty-four workers fighting over eight cards.
_LAUNCHERS: Final = ("torchrun", "torch.distributed.run", "torch.distributed.launch")


@dataclass(frozen=True)
class ExpertMesh:
    """How the ranks of one run are arranged, and what that costs in traffic.

    ``expert_parallel`` is one number wearing two hats and that is OLMo-core's doing rather
    than this module's: on ``edullm/final-model`` the HSDP shard degree and the expert-parallel
    degree are both ``--moe-shard-degree``, so a mesh cannot shard the model over a different
    set of ranks than it shards the experts over.
    """

    nodes: int
    gpus_per_node: int
    expert_parallel: int
    routed_experts: int = ROUTED_EXPERTS

    @property
    def world_size(self) -> int:
        return self.nodes * self.gpus_per_node

    @property
    def replicas(self) -> int:
        """``--moe-num-replicas``, derived rather than asked for.

        OLMo-core refuses unless ``num_replicas * shard_degree`` is exactly the world size, so
        there is only ever one answer and asking a person for it is asking them to reproduce a
        division. Zero when the degree does not divide the world size, which
        :func:`mesh_refusals` is what reports.
        """
        if self.expert_parallel <= 0 or self.world_size % self.expert_parallel:
            return 0
        return self.world_size // self.expert_parallel

    @property
    def experts_per_rank(self) -> int:
        if self.expert_parallel <= 0 or self.routed_experts % self.expert_parallel:
            return 0
        return self.routed_experts // self.expert_parallel

    @property
    def all_to_all_crosses_the_fabric(self) -> bool:
        """Whether the MoE all-to-all leaves the machine it started on.

        The whole reason this property exists: an expert-parallel group that fits inside one
        node's cards exchanges tokens over NVLink, and one that does not exchanges them over
        the network between machines, every layer, every step, in both directions. It is not
        an error and nothing will say so.
        """
        return self.expert_parallel > self.gpus_per_node

    def describe(self) -> str:
        return (
            f"{self.world_size} ranks: {self.replicas} replicas x {self.expert_parallel} "
            f"expert-parallel, {self.experts_per_rank} of {self.routed_experts} experts a rank"
        )


def expert_parallel_choices(
    *, gpus_per_node: int, routed_experts: int = ROUTED_EXPERTS
) -> tuple[int, ...]:
    """Every expert-parallel degree the recipe can express, ascending.

    The divisors of the expert count and nothing else, which is OLMo-core's rule rather than a
    preference: a degree that does not divide it leaves some rank owning a fraction of an
    expert. Reported to a person who asked for a degree that is not one of these, because the
    useful half of that refusal is the list of the ones that are.
    """
    return tuple(
        degree
        for degree in range(1, max(routed_experts, gpus_per_node) + 1)
        if routed_experts % degree == 0
    )


def node_local_expert_parallel(
    *, gpus_per_node: int, routed_experts: int = ROUTED_EXPERTS
) -> int:
    """The widest expert-parallel group that still fits inside one machine.

    **THIS IS THE DEFAULT AND IT DISAGREES WITH THE ONE OLMo-core SHIPS**, which is 32 against
    a world of 64 -- two replicas of a shard group spanning four machines. That layout is
    validated in the sense that its arithmetic is checked; it is not the layout the design
    argued for. Thirty-two experts fit across one node's eight cards, so at a degree of eight
    every token the router sends lands on a card connected by NVLink, and the only thing left
    crossing the network between machines is the gradient reduction, once a step, which
    overlaps with the backward pass. At a degree of 32 the same all-to-all crosses the network
    twice per MoE layer per step and overlaps with nothing.

    Falls back to the largest divisor at or below the card count, so a shape with six cards and
    32 experts answers four rather than refusing -- the point is to stay inside the machine.
    """
    inside = [
        degree
        for degree in expert_parallel_choices(
            gpus_per_node=gpus_per_node, routed_experts=routed_experts
        )
        if degree <= gpus_per_node
    ]
    return max(inside) if inside else 1


def mesh_for(
    *,
    nodes: int,
    gpus_per_node: int,
    expert_parallel: int | None = None,
    routed_experts: int = ROUTED_EXPERTS,
) -> ExpertMesh:
    """The mesh for a node count, with the node-local degree unless somebody named one."""
    degree = (
        node_local_expert_parallel(gpus_per_node=gpus_per_node, routed_experts=routed_experts)
        if expert_parallel is None
        else expert_parallel
    )
    return ExpertMesh(
        nodes=nodes,
        gpus_per_node=gpus_per_node,
        expert_parallel=degree,
        routed_experts=routed_experts,
    )


def mesh_refusals(mesh: ExpertMesh) -> tuple[str, ...]:
    """Every reason OLMo-core would reject this mesh, transcribed from its own validator.

    Restated here rather than discovered on the machines, because the alternative is finding
    out from sixty-four processes that each cloned a branch, imported torch, opened a
    rendezvous and then refused -- after the claims are taken and the containers are up. The
    rules are ``validate_olmoe_parallelism`` in ``.edullm/train_on_corpus.py``: a positive
    degree, a degree that divides the expert count, and a world size that is exactly the
    product of the replica count and the degree.
    """
    refusals: list[str] = []
    if mesh.nodes < 1:
        refusals.append(f"node_count_is_not_positive:{mesh.nodes}")
    if mesh.gpus_per_node < 1:
        refusals.append(f"cards_per_node_is_not_positive:{mesh.gpus_per_node}")
    if mesh.expert_parallel < 1:
        refusals.append(f"expert_parallel_is_not_positive:{mesh.expert_parallel}")
        return tuple(refusals)
    if mesh.routed_experts % mesh.expert_parallel:
        refusals.append(
            f"expert_parallel_does_not_divide_the_experts:{mesh.expert_parallel} against "
            f"{mesh.routed_experts} routed experts. OLMo-core accepts "
            f"{expert_parallel_choices(gpus_per_node=mesh.gpus_per_node, routed_experts=mesh.routed_experts)}"
        )
    if mesh.world_size % mesh.expert_parallel:
        refusals.append(
            f"expert_parallel_does_not_divide_the_world:{mesh.expert_parallel} against "
            f"{mesh.world_size} ranks, so no replica count multiplies out to it"
        )
    return tuple(refusals)


@dataclass(frozen=True)
class Candidate:
    """One machine this run could use, reduced to what a launch needs from it.

    ``private_ip`` is not optional here even though it is on ``FleetNode``. A node with no
    private address cannot host a rendezvous and cannot reach one, so an instance in that state
    is a blocker rather than a candidate, and carrying the ``None`` any further would push the
    check into the string that builds the endpoint.
    """

    node: int
    instance_id: str
    private_ip: str


@dataclass(frozen=True)
class NodeChoice:
    """The whole set, or every reason there is not one.

    Both fields are populated on a refusal: ``chosen`` is what would have been taken, which is
    worth printing beside the blockers so a person can see how close the fleet is.
    """

    chosen: tuple[Candidate, ...]
    refusals: tuple[str, ...]

    @property
    def usable(self) -> bool:
        return not self.refusals and bool(self.chosen)


def blocker_for(
    *,
    node: int,
    fleet: Mapping[int, FleetNode],
    readings: Mapping[int, NodeReading],
    run: str,
    force: bool,
) -> str | None:
    """Why this node cannot join the run, or ``None`` if it can.

    The order is the order the answers stop being trustworthy in. An instance that is not in
    the fleet has no reading worth consulting; a node that did not answer has a reading that
    says nothing about whether it is busy; and a node whose bootstrap never finished answers
    the busy question perfectly while being the one machine that can run nothing. Reversing any
    two of these produces a refusal that names a symptom of the one above it.
    """
    found = fleet.get(node)
    if found is None:
        return f"node {node} is not a running instance in this fleet"
    if found.private_ip is None:
        return (
            f"node {node} ({found.instance_id}) reports no private address, so nothing can "
            "rendezvous with it"
        )
    reading = readings.get(node)
    if reading is None or not reading.reachable:
        detail = reading.detail if reading is not None else "no reading"
        return f"node {node} ({found.instance_id}) did not answer: {detail}"
    if not reading.ready:
        return (
            f"node {node} ({found.instance_id}) never wrote its readiness sentinel, so it has "
            "no scratch filesystem and no pre-pulled image"
        )
    if force:
        return None
    if reading.run is not None and reading.run != run:
        return (
            f"node {node} is held by {reading.who or 'somebody'} for {reading.run}"
        )
    if reading.run is None and reading.gpus_busy > 0:
        return (
            f"node {node} has {reading.gpus_busy} cards in use with nothing claiming them, "
            "which is a run somebody started outside the workflow"
        )
    return None


def choose_nodes(
    *,
    fleet: Sequence[FleetNode],
    readings: Sequence[NodeReading],
    requested: Sequence[int] = (),
    node_count: int | None = None,
    run: str,
    force: bool = False,
) -> NodeChoice:
    """The set of machines this run gets, decided in one place before anything is written.

    **NAMED NODES AND A COUNT ARE DIFFERENT QUESTIONS AND ONLY ONE MAY BE ASKED.** A named list
    is a person saying which machines, usually because one of them is being held back as the
    downstream lane; a count is a person saying how many and letting this pick. Accepting both
    would mean silently ignoring one of them, and the ignored one is whichever the caller cared
    about.

    A named node that is blocked is a refusal rather than a substitution. Somebody who asked
    for nodes 1 through 7 because node 8 is reserved for evaluation does not want node 8
    quietly used because node 4 was busy.
    """
    if bool(requested) == (node_count is not None):
        return NodeChoice(
            chosen=(),
            refusals=(
                (
                    "name the nodes or give a count, and not both: a list says which machines "
                    "and a count says how many, and honouring both means ignoring one"
                ),
            ),
        )

    by_number = {found.node: found for found in fleet if found.node is not None}
    read_by_number = {
        reading.node: reading for reading in readings if reading.node is not None
    }

    def candidate(number: int) -> Candidate:
        found = by_number[number]
        assert found.private_ip is not None  # blocker_for refuses before this is reached
        return Candidate(
            node=number, instance_id=found.instance_id, private_ip=found.private_ip
        )

    if requested:
        wanted = sorted(set(requested))
        if len(wanted) != len(requested):
            return NodeChoice(
                chosen=(),
                refusals=(f"the node list repeats a number: {list(requested)}",),
            )
        named_blockers = tuple(
            reason
            for number in wanted
            if (
                reason := blocker_for(
                    node=number,
                    fleet=by_number,
                    readings=read_by_number,
                    run=run,
                    force=force,
                )
            )
            is not None
        )
        if named_blockers:
            return NodeChoice(chosen=(), refusals=named_blockers)
        return NodeChoice(chosen=tuple(candidate(number) for number in wanted), refusals=())

    assert node_count is not None
    if node_count < 1:
        return NodeChoice(chosen=(), refusals=(f"node_count_is_not_positive:{node_count}",))

    free: list[int] = []
    blocked: list[str] = []
    for number in sorted(by_number):
        reason = blocker_for(
            node=number, fleet=by_number, readings=read_by_number, run=run, force=force
        )
        if reason is None:
            free.append(number)
        else:
            blocked.append(reason)

    if len(free) < node_count:
        return NodeChoice(
            chosen=tuple(candidate(number) for number in free),
            refusals=(
                (
                    f"the fleet has {len(free)} nodes this run could take and it asked for "
                    f"{node_count}"
                ),
                *blocked,
            ),
        )
    return NodeChoice(chosen=tuple(candidate(number) for number in free[:node_count]), refusals=())


@dataclass(frozen=True)
class Rendezvous:
    """Where the ranks find each other, and under what name.

    ``run_id`` is the run name rather than something minted per dispatch, and that is a control
    rather than an economy. A second dispatch of a run that is already up meets the first one's
    rendezvous, which has closed, and is refused by torchrun -- where a fresh id would have
    formed a second job of the same name on the same cards.
    """

    host: Candidate
    port: int
    run_id: str

    @property
    def endpoint(self) -> str:
        return f"{self.host.private_ip}:{self.port}"


def rendezvous_for(
    chosen: Sequence[Candidate], *, run: str, port: int = DEFAULT_RENDEZVOUS_PORT
) -> Rendezvous:
    """Elect the lowest-numbered node as the rendezvous host.

    Lowest rather than any, because the election has to give the same answer to a re-dispatch
    as it gave to the first one. A rule that picked the first machine some API happened to
    return would move the store between attempts, and the attempt that moved it would leave the
    previous endpoint being dialled by whatever was still up.

    The private address and never the hostname. Every node in this fleet resolves its own
    ``ip-10-x-y-z`` name and none of them resolve each other's unless the VPC is running DNS
    hostnames for the subnet, which is not something this lane arranges.
    """
    if not chosen:
        raise ValueError("a rendezvous needs at least one node")
    host = min(chosen, key=lambda candidate: candidate.node)
    return Rendezvous(host=host, port=port, run_id=run)


def command_refusals(command: str) -> tuple[str, ...]:
    """Whether the training command is the thing this path is going to wrap, or already wrapped.

    What belongs here is the entrypoint and its arguments -- ``python .edullm/train.py --flag``
    -- because the rendezvous flags depend on which machines were claimed and cannot be written
    into a branch days earlier. A command that already names a launcher is about to be launched
    twice: eight agents per node, each starting eight workers that each start eight more.

    **THE REST OF THESE ARE THE SHAPES ``--no-python`` CANNOT EXEC, AND EACH OF THEM IS QUIET
    EVERYWHERE ELSE.** :func:`torchrun_command` passes that flag, so torchrun execs the first
    word instead of handing it to an interpreter and the first word has to name a program.
    Both of the spellings that do not are things somebody would reasonably write, both are
    accepted by the workflow form, by ``yaml.safe_load`` and by every check upstream of here,
    and both arrive as sixty-four identical failures a minute after the containers are up. The
    refusal costs a second at dispatch; the alternative costs what is left of the window.
    """
    refusals: list[str] = []
    if not command.strip():
        return ("the command resolved to nothing",)
    for launcher in _LAUNCHERS:
        if launcher in command:
            refusals.append(
                f"the command already names {launcher!r}, and this path wraps it in one. "
                "Pass the entrypoint and its arguments only; the rendezvous flags are decided "
                "here because they depend on which nodes were claimed."
            )
    try:
        words = shlex.split(command)
    except ValueError as error:
        # There is nothing to read the first word off, so the two checks below are skipped
        # rather than guessed at. The quote itself is worth refusing on its own account: the
        # composed line is re-split by the container's own `bash -lc`, so a quote that never
        # closes swallows the redirection and the log pipe written after the command.
        return (
            *refusals,
            (
                f"the command does not parse as a shell line: {error}. The launch line is "
                "re-split by the shell inside the container, so an unclosed quote takes the "
                "redirection and the `tee` that follow it into the argument it opened."
            ),
        )
    first = words[0]
    if first.endswith(".py"):
        refusals.append(
            f"the command starts with {first!r}, which is a script rather than a program. "
            "torchrun is given --no-python here and execs the first word itself, so a `.py` "
            "path has to carry an executable bit and a shebang -- and what a fresh `git "
            "clone` gives it is neither. Name the interpreter: `python <script> <flags>`."
        )
    if "=" in first:
        refusals.append(
            f"the command starts with {first!r}, which is an environment assignment rather "
            "than a program. A shell would set it and run what follows; torchrun execs the "
            "first word, and there is no file whose name has an equals sign in it. Whatever "
            "the variable was reaching for belongs in the entrypoint, which is where "
            "`.edullm/train_on_corpus.py` puts its own clone on `sys.path`."
        )
    return tuple(refusals)


def run_name_refusals(run: str) -> tuple[str, ...]:
    """Whether the run name survives being written into a line that is split on whitespace.

    **IT IS THE ONLY VALUE IN THE COMPOSED LINE THAT IS NEITHER A NUMBER NOR READ BACK OUT OF
    EC2, AND NOTHING QUOTES IT.** :func:`torchrun_command` joins its parts with a space and the
    run name goes in as ``--rdzv-id=<name>``, so a name carrying whitespace is not one flag
    with an odd value -- it is two words. What torchrun does with the second is not an error.
    It takes it as the positional and everything after it as arguments to it, which means
    ``--rdzv-backend`` is consumed rather than read: driven through
    ``torch.distributed.run.parse_args``, ``--rdzv-id=final model a`` gives ``rdzv_id='final'``,
    ``rdzv_backend='static'`` and an empty endpoint. The rendezvous form this whole module is
    built to guarantee silently becomes the static form with no ranks assigned.

    ``.github/workflows/block-run-distributed.yml`` refuses a name outside
    ``[A-Za-z0-9][A-Za-z0-9._-]{0,63}`` before it reaches the tool, and that is the form. It is
    not the tool, which a maintainer runs from a laptop when GitHub is the thing that is
    broken, with ``--run`` taking whatever was typed and nobody else reading it.
    """
    if not run.strip():
        return (
            (
                "the run name is empty, and it names the rendezvous, the claim on every node, "
                "the container and the S3 prefix"
            ),
        )
    hostile = sorted({found for found in run if found.isspace() or found in "'\""})
    if hostile:
        return (
            (
                f"the run name {run!r} carries {hostile}, and it is written into the launch "
                "line as --rdzv-id=<name> with nothing quoting it. torchrun reads everything "
                "past the space as arguments to a positional it took from the middle of the "
                "name, so --rdzv-backend is never read and the job forms in the static form "
                "with no ranks assigned. Letters, digits, dot, dash and underscore, which is "
                "what the workflow form already asks for."
            ),
        )
    return ()


def with_mesh_flags(command: str, *, mesh: ExpertMesh) -> tuple[str, tuple[str, ...]]:
    """The command with the mesh it is going to run on, or why the two disagree.

    **THE DISAGREEMENT IS THE POINT AND IT IS SILENT WITHOUT THIS.** A command carrying
    ``--moe-shard-degree 32`` dispatched onto seven nodes is refused by OLMo-core, loudly, and
    that case takes care of itself. The same command dispatched onto eight is accepted, runs,
    trains correctly and spends every MoE all-to-all on the network between machines. Nothing
    downstream can tell that apart from the mesh somebody meant.

    So a command that names either flag has to name both and both have to be the ones this
    module computed, and a command that names neither is given them. Neither branch guesses.
    """
    named = {flag: _flag_value(command, flag) for flag in MESH_FLAGS}
    present = {flag: value for flag, value in named.items() if value is not None}
    if not present:
        return (
            f"{command} {MESH_FLAGS[0]} {mesh.expert_parallel} "
            f"{MESH_FLAGS[1]} {mesh.replicas}"
        ), ()
    if len(present) != len(MESH_FLAGS):
        missing = [flag for flag in MESH_FLAGS if flag not in present]
        return command, (
            (
                f"the command names {sorted(present)} and not {missing}. The two describe one "
                "mesh and OLMo-core multiplies them out against the world size, so half of it "
                "is a world size nobody chose."
            ),
        )
    wanted = {MESH_FLAGS[0]: mesh.expert_parallel, MESH_FLAGS[1]: mesh.replicas}
    wrong = tuple(
        f"the command says {flag}={present[flag]} and {mesh.nodes} nodes of "
        f"{mesh.gpus_per_node} cards needs {wanted[flag]}"
        for flag in MESH_FLAGS
        if present[flag] != str(wanted[flag])
    )
    return command, wrong


def _flag_value(command: str, flag: str) -> str | None:
    """What a flag was given, whether it was written with a space or an equals sign.

    Both spellings reach argparse identically and both are written by hand in this project, so
    a reader that understood one of them would report the other as absent -- and absent is the
    branch that appends a second copy of the flag.
    """
    words = command.split()
    for index, word in enumerate(words):
        if word == flag and index + 1 < len(words):
            return words[index + 1]
        if word.startswith(f"{flag}="):
            return word.split("=", 1)[1]
    return None


def torchrun_command(
    *,
    mesh: ExpertMesh,
    rendezvous: Rendezvous,
    command: str,
    max_restarts: int = 0,
    join_timeout_seconds: int = 900,
    launcher: str = "torchrun",
) -> str:
    """The one line every node runs, identical on all of them.

    **THERE IS NO ``--node-rank`` HERE AND THERE MUST NEVER BE ONE.** It belongs to torchrun's
    static form and is ignored the moment ``--rdzv-backend`` is set, so a command carrying both
    reads as though it assigns ranks and does not. The rendezvous decides the ranks, which is
    also why one Systems Manager call can carry this to every machine at once: there is nothing
    in it that differs per node, so there is nothing to get wrong per node.

    ``--max-restarts=0`` is how the job fails as a unit. Torchrun stops every surviving worker
    the moment any of them fails, and then either forms a new group or gives up; at zero it
    gives up, which is the behaviour wanted on a fleet where a silent restart would leave the
    ranks disagreeing about which step they are on. Above zero it would quietly re-form the
    group with a different rank assignment, and a checkpoint written either side of that is a
    checkpoint written by a different layout.

    ``join_timeout`` is generous because the nodes are started by one fan-out and do not arrive
    together: a clone of the branch is tens of seconds and a cold page cache makes it longer on
    some machines than others. Its cost when something is genuinely wrong is that the healthy
    nodes wait this long before saying so, which is the right trade against a job that gives up
    because one machine was slow to clone.

    **``--no-python`` IS WHAT MAKES THE POSITIONAL A PROGRAM, AND THE LINE STARTS NOTHING
    WITHOUT IT.** torchrun's positional is a *script path*: ``config_from_args`` in
    ``torch/distributed/run.py`` builds the child argv as ``[sys.executable, "-u",
    training_script, *args]``, so it supplies the interpreter itself. What arrives here is a
    command line rather than a script path, and that is the contract every other surface in
    this lane already has -- ``.edullm/run.yaml`` carries ``python .edullm/train_on_corpus.py
    --flags`` and ``edullm-node run`` feeds that same string to ``bash -lc``. Appended to the
    flags unchanged it becomes ``python -u python .edullm/train_on_corpus.py --flags``, which
    is Python being asked to open a file called ``python``, on all sixty-four ranks, seconds
    after the containers come up and after the window has been paid for.

    **STRIPPING A LEADING ``python`` WAS THE OTHER CANDIDATE AND IT ASSUMES THE ONE THING
    NOTHING PROMISES.** It keeps torchrun's own ``-u`` and it is correct for today's command.
    It is wrong for any command whose first word is not an interpreter, and it is wrong
    silently: ``bash -lc '...'`` matches no spelling of ``python``, so nothing is stripped and
    torchrun runs ``python -u bash -lc '...'`` -- the same start-up failure on the same
    sixty-four ranks, with a different filename in it. A rule that has to recognise every way
    of writing "an interpreter" in order to leave a command intact is a rule that corrupts the
    spelling nobody thought of, and the two paths that produce a command here are a free-text
    workflow input and a YAML file on somebody else's branch. ``--no-python`` recognises
    nothing, so it has nothing to get wrong; what it cannot exec is refused by
    :func:`command_refusals` at dispatch instead.

    **WHAT IT COSTS IS TORCHRUN'S ``-u``, AND THAT IS PAID FOR IN THE CONTAINER.** With an
    interpreter prepended torchrun asked for unbuffered output; exec'ing the program directly
    cannot. Sixty-four ranks block-buffering stdout into the pipe feeding ``tee`` is a loss
    exactly when it is least affordable: a process that dies takes whatever had not yet filled
    its buffer with it, and on a rank that died during start-up that is the whole of what it
    had to say. ``infra/block-distributed-launch.sh`` sets ``PYTHONUNBUFFERED=1`` on the
    container for that reason, and ``tests/test_block_distributed_tool.py`` holds the flag and
    the variable against each other so neither can be removed alone.
    """
    return " ".join(
        (
            launcher,
            f"--nnodes={mesh.nodes}",
            f"--nproc-per-node={mesh.gpus_per_node}",
            f"--max-restarts={max_restarts}",
            f"--rdzv-id={rendezvous.run_id}",
            "--rdzv-backend=c10d",
            f"--rdzv-endpoint={rendezvous.endpoint}",
            f"--rdzv-conf=join_timeout={join_timeout_seconds}",
            "--no-python",
            command,
        )
    )


def cards_per_node(
    chosen: Sequence[Candidate], readings: Sequence[NodeReading]
) -> tuple[int, tuple[str, ...]]:
    """How many cards each machine has, read off the machines rather than off the shape.

    ``--nproc-per-node`` is one number for the whole job -- torchrun only supports a homogeneous
    local world size -- so a fleet whose nodes disagree is a refusal rather than a minimum. It
    is reachable rather than theoretical: a node whose driver enumerated seven devices answers
    the readiness probe with a seven, and the launch that would follow is sixty-three ranks
    waiting on a sixty-fourth that no machine is going to start.
    """
    counts = {reading.node: reading.gpus_total for reading in readings if reading.node is not None}
    seen = {counts.get(candidate.node, 0) for candidate in chosen}
    if not seen:
        return 0, ("no node was chosen, so there is no card count to read",)
    if len(seen) != 1 or 0 in seen:
        return 0, (
            "the chosen nodes do not all report the same number of cards: "
            + ", ".join(
                f"node {candidate.node} reports {counts.get(candidate.node, 0)}"
                for candidate in chosen
            ),
        )
    return seen.pop(), ()


@dataclass(frozen=True)
class LaunchPlan:
    """Everything decided before anything is claimed, and every reason it cannot be.

    Built from a fleet listing and a set of readings and nothing else, so the whole of what a
    dispatch is going to do is decided by a function a test can call. What is left for
    ``tools/block_run_distributed.py`` is the four Systems Manager calls that carry it out.
    """

    chosen: tuple[Candidate, ...]
    mesh: ExpertMesh
    rendezvous: Rendezvous | None
    launch_command: str
    training_command: str
    refusals: tuple[str, ...]

    @property
    def usable(self) -> bool:
        return not self.refusals and self.rendezvous is not None


def plan_launch(
    *,
    fleet: Sequence[FleetNode],
    readings: Sequence[NodeReading],
    run: str,
    command: str,
    requested: Sequence[int] = (),
    node_count: int | None = None,
    expert_parallel: int | None = None,
    routed_experts: int = ROUTED_EXPERTS,
    port: int = DEFAULT_RENDEZVOUS_PORT,
    max_restarts: int = 0,
    join_timeout_seconds: int = 900,
    mesh_flags: bool = True,
    force: bool = False,
) -> LaunchPlan:
    """The whole dispatch, decided in one pass, before a single claim is written.

    **THE ORDER IS THE CHEAPEST REFUSAL FIRST AND IT IS NOT ARBITRARY.** A node set that cannot
    be assembled makes every question after it unanswerable -- there is no card count without
    nodes and no mesh without a card count -- so the blockers come out whole and the rest is not
    guessed at. Everything here is free; the first thing that is not free is the claim, and by
    then there is nothing left to find out.

    It collects rather than raising, and that is the property the caller depends on. Somebody
    told their command names the wrong shard degree will fix it, dispatch again, and be told
    their node list has a machine somebody else is on. Both were knowable in the first pass.
    """
    refusals: list[str] = []
    choice = choose_nodes(
        fleet=fleet,
        readings=readings,
        requested=requested,
        node_count=node_count,
        run=run,
        force=force,
    )
    refusals.extend(choice.refusals)
    refusals.extend(command_refusals(command))
    refusals.extend(run_name_refusals(run))

    cards, card_refusals = (
        cards_per_node(choice.chosen, readings) if choice.chosen else (0, ())
    )
    refusals.extend(card_refusals)

    mesh = mesh_for(
        nodes=len(choice.chosen),
        gpus_per_node=cards,
        expert_parallel=expert_parallel,
        routed_experts=routed_experts,
    )
    if choice.chosen and cards:
        refusals.extend(mesh_refusals(mesh))

    training_command = command
    if mesh_flags and not refusals:
        training_command, flag_refusals = with_mesh_flags(command, mesh=mesh)
        refusals.extend(flag_refusals)

    if refusals:
        return LaunchPlan(
            chosen=choice.chosen,
            mesh=mesh,
            rendezvous=None,
            launch_command="",
            training_command=training_command,
            refusals=tuple(refusals),
        )

    rendezvous = rendezvous_for(choice.chosen, run=run, port=port)
    return LaunchPlan(
        chosen=choice.chosen,
        mesh=mesh,
        rendezvous=rendezvous,
        launch_command=torchrun_command(
            mesh=mesh,
            rendezvous=rendezvous,
            command=training_command,
            max_restarts=max_restarts,
            join_timeout_seconds=join_timeout_seconds,
        ),
        training_command=training_command,
        refusals=(),
    )


@dataclass(frozen=True)
class NodeOutcome:
    """What one node said to one phase of the launch.

    ``status`` is the Systems Manager invocation status and it decides everything. A node with
    no invocation at all gets one of these too, with a status saying so, because the whole
    property this module is defending is that a node which did not answer is not a node that
    succeeded -- and a reader built on "was there output" cannot tell those apart.
    """

    node: int | None
    instance_id: str
    status: str
    output: str
    error: str

    @property
    def ok(self) -> bool:
        return self.status == "Success"


def outcomes(
    chosen: Sequence[Candidate], invocations: Mapping[str, Mapping[str, Any]]
) -> tuple[NodeOutcome, ...]:
    """One outcome per node asked, in node order, whether or not the node answered."""
    found: list[NodeOutcome] = []
    for candidate in chosen:
        invocation = invocations.get(candidate.instance_id)
        if invocation is None:
            found.append(
                NodeOutcome(
                    node=candidate.node,
                    instance_id=candidate.instance_id,
                    status="no invocation",
                    output="",
                    error="Systems Manager produced no invocation for this instance",
                )
            )
            continue
        found.append(
            NodeOutcome(
                node=candidate.node,
                instance_id=candidate.instance_id,
                status=str(invocation.get("Status") or ""),
                output=str(invocation.get("StandardOutputContent") or ""),
                error=str(invocation.get("StandardErrorContent") or ""),
            )
        )
    return tuple(found)


def refused(found: Iterable[NodeOutcome]) -> tuple[NodeOutcome, ...]:
    return tuple(outcome for outcome in found if not outcome.ok)


def launch_markdown(
    *,
    run: str,
    mesh: ExpertMesh,
    rendezvous: Rendezvous,
    chosen: Sequence[Candidate],
    fabric: Mapping[int, str],
    command: str,
    bucket: str,
    reservation_id: str,
    region: str,
    entity: str,
    project: str,
) -> str:
    """The page somebody reads thirty seconds after dispatching, in the order they read it.

    The fabric column is first among the things that are not obvious, and it is here rather
    than in a log because it is the one property of this run that is invisible afterwards and
    decides how long it takes. ``tcp`` means the gradient reduction between machines is going
    over the ordinary network interface, which works and is several times slower than the
    fabric these machines were bought for.
    """
    crossing = (
        "the fabric between machines"
        if mesh.all_to_all_crosses_the_fabric
        else "NVLink inside each machine"
    )
    lines = [
        f"### `{run}` on {mesh.nodes} nodes",
        "",
        f"| world size | {mesh.world_size} ranks |",
        "| --- | --- |",
        f"| mesh | {mesh.describe()} |",
        f"| MoE all-to-all | {crossing} |",
        f"| rendezvous | `{rendezvous.endpoint}` on node {rendezvous.host.node} |",
        "| launcher | `torchrun --rdzv-backend=c10d`, identical on every node |",
        "",
        "```",
        f"{'node':<6}{'instance':<21}{'private ip':<17}fabric",
        "-" * 50,
    ]
    for candidate in chosen:
        lines.append(
            f"{candidate.node:<6}{candidate.instance_id:<21}{candidate.private_ip:<17}"
            f"{fabric.get(candidate.node, 'unknown')}"
        )
    lines += ["```", ""]

    # SAID HERE RATHER THAN LEFT IN A LOG, BECAUSE IT IS THE ONE PROPERTY OF THIS RUN THAT IS
    # INVISIBLE AFTERWARDS AND DECIDES HOW LONG IT TAKES. A p5 carries no EFA unless
    # `run-instances` named the interfaces, so a fleet launched without them has the driver, the
    # plugin and no device -- and NCCL falls back to the ordinary interface with nothing anywhere
    # reporting a problem. What that looks like from the outside is a run whose loss falls
    # correctly and whose steps take several times what they should.
    if any(found == "tcp" for found in fabric.values()):
        lines += [
            (
                "**THIS JOB IS NOT USING THE FABRIC.** At least one node reports `tcp`, which "
                "means the gradient reduction between machines is going over the ordinary "
                "network interface. A `p5.48xlarge` carries no EFA device unless the launch "
                "asked for one, so this node was launched without them -- either with "
                "`efa_interfaces=0`, or by a dispatch that asked for `fabric=tcp`. The run is "
                "correct and it is slower; the fix is at launch time, not here."
            ),
            "",
        ]

    lines += [
        "The command every rank is running:",
        "",
        "```",
        command,
        "```",
        "",
        (
            "Rank 0's log is the one to read; the others are the same run seen from another "
            "machine. Logs are synced to S3 once a minute and reading them needs an AWS "
            "credential, so the Weights and Biases page is the surface for anybody who holds "
            "none."
        ),
        "",
        f"| W&B | https://wandb.ai/{entity}/{project}/runs/{run} |",
        "| --- | --- |",
        "",
        "```",
        f"s3://{bucket}/block/{reservation_id}/node-{rendezvous.host.node}/{run}/log/train.log",
        "```",
        "",
        "With a role and the Session Manager plugin, attach to rank 0 directly:",
        "",
        "```bash",
        f"aws ssm start-session --target {rendezvous.host.instance_id} --region {region} \\",
        "  --document-name AWS-StartInteractiveCommand \\",
        f"  --parameters '{{\"command\":[\"sudo docker logs -f edullm-{run}\"]}}'",
        "```",
    ]
    return "\n".join(lines)
