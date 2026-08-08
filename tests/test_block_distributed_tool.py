"""The four Systems Manager phases of a multi-node launch, with the AWS CLI replaced.

What is worth testing here is not that Systems Manager works. It is the handful of states this
tool can leave the fleet in on the one morning it matters:

* claims taken on some machines and not others, with nothing running -- which reads to
  everybody else in the window as a fleet that is fully occupied;
* containers started on some machines and not others, sitting at a rendezvous that will never
  complete, holding cards nobody can use;
* a rollback that stopped at its first failure and therefore left the mess it was called to
  clear;
* and a node that Systems Manager never reached, counted as a node that succeeded.

The last one is the load-bearing case and it is the one an obvious implementation gets wrong:
the invocation list contains an entry per machine *reached*, so a reader that iterates the
answers rather than the machines finds every answer successful.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from edullm_platform.block_fleet import NODE_TAG, RESERVATION_TAG
from tools import block_run_distributed

BLOCK = "cr-0afc33f3a1af417a7"
TRAINING = "python .edullm/train_on_corpus.py --model-factory=olmoe_7b_32x4"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAUNCH_SCRIPT = PROJECT_ROOT / "infra" / "block-distributed-launch.sh"
BOOTSTRAP = PROJECT_ROOT / "infra" / "block-node-bootstrap.sh"

#: Every ``${EDULLM_DIST_X:?}`` the launch script refuses to start without.
REQUIRED_SETTING = re.compile(r"\$\{(EDULLM_DIST_[A-Z_]+):\?")

#: A ``printf`` format writing a flat JSON object whose every value is a quoted string, which
#: is the claim file and nothing else in either script. The drain record next to it in the
#: bootstrap is the near miss this has to exclude, and it is excluded by carrying ``"node":%s``
#: unquoted rather than by being named here.
CLAIM_RECORD = re.compile(r'\{(?:"[a-z_]+":"%s",)+"[a-z_]+":"%s"\}')


def instance(number: int) -> dict[str, Any]:
    return {
        "InstanceId": f"i-{number:017d}",
        "State": {"Name": "running"},
        "PrivateIpAddress": f"172.31.0.{number}",
        "Tags": [
            {"Key": NODE_TAG, "Value": str(number)},
            {"Key": RESERVATION_TAG, "Value": BLOCK},
        ],
    }


def probe_output(*, ready: bool = True, busy: int = 0, run: str | None = None) -> str:
    lines = ["gpus_total\t8", f"gpus_busy\t{busy}"]
    if run is not None:
        lines += [f"run\t{run}", "who\teric"]
    if ready:
        lines.append("ready\ttrue")
    return "\n".join(lines) + "\n"


def started_output(node: int, *, fabric: str = "tcp") -> str:
    return (
        f"node\t{node}\n"
        "commit\t8d60e6c0000000000000000000000000000000ab\n"
        f"fabric\t{fabric}\n"
        "container\tedullm-final-model-a\n"
    )


class FakeCli:
    """Answers each phase by the comment its ``send-command`` carried.

    Keyed on the comment rather than on the command id, because what a test wants to say is
    "the claim refused on node three" rather than "the second command refused on node three" --
    and because a phase the tool stops skipping is then a missing key rather than an answer
    intended for something else.
    """

    def __init__(self, *, fleet: Sequence[int], phases: dict[str, dict[int, dict[str, Any]]]):
        self.fleet = list(fleet)
        self.phases = phases
        self.calls: list[list[str]] = []
        self.sent: dict[str, str] = {}
        self.commands: dict[str, str] = {}

    def phase_of(self, comment: str) -> str:
        for name in ("probe", "claim", "start", "rollback"):
            if name in comment:
                return name
        raise AssertionError(f"no phase in comment {comment!r}")

    def __call__(
        self, arguments: Sequence[str], *, profile: str | None = None, region: str | None = None
    ) -> Any:
        argv = list(arguments)
        self.calls.append(argv)
        head = " ".join(argv[:2])
        if head == "ec2 describe-instances":
            if "--query" in argv:
                return [[BLOCK]]
            return {"Reservations": [{"Instances": [instance(n)]} for n in self.fleet]}
        if head == "ssm send-command":
            phase = self.phase_of(argv[argv.index("--comment") + 1])
            self.commands[phase] = argv[argv.index("--parameters") + 1]
            self.sent[f"cmd-{phase}"] = phase
            return {"Command": {"CommandId": f"cmd-{phase}"}}
        if head == "ssm list-command-invocations":
            phase = self.sent[argv[argv.index("--command-id") + 1]]
            return {
                "CommandInvocations": [
                    {"InstanceId": f"i-{node:017d}", "Status": answer["Status"]}
                    for node, answer in self.phases[phase].items()
                ]
            }
        if head == "ssm get-command-invocation":
            phase = self.sent[argv[argv.index("--command-id") + 1]]
            wanted = argv[argv.index("--instance-id") + 1]
            for node, answer in self.phases[phase].items():
                if f"i-{node:017d}" == wanted:
                    return {"InstanceId": wanted, **answer}
            raise AssertionError(f"no answer for {wanted} in {phase}")
        raise AssertionError(f"unexpected call {head}")

    def instance_ids_for(self, phase: str) -> list[str]:
        for argv in self.calls:
            if argv[:2] == ["ssm", "send-command"] and phase in argv[argv.index("--comment") + 1]:
                start = argv.index("--instance-ids") + 1
                end = argv.index("--timeout-seconds")
                return argv[start:end]
        return []

    def reached(self, phase: str) -> bool:
        return any(
            argv[:2] == ["ssm", "send-command"]
            and phase in argv[argv.index("--comment") + 1]
            for argv in self.calls
        )


def answered(nodes: Sequence[int], *, status: str = "Success", output: str = "") -> dict[
    int, dict[str, Any]
]:
    return {
        node: {"Status": status, "StandardOutputContent": output, "StandardErrorContent": ""}
        for node in nodes
    }


def arguments(*extra: str) -> Any:
    return block_run_distributed.build_parser().parse_args(
        [
            "--run",
            "final-model-a",
            "--branch",
            "edullm/final-model",
            "--command",
            TRAINING,
            "--no-profile",
            "--wait-seconds",
            "0",
            "--start-wait-seconds",
            "0",
            *extra,
        ]
    )


@pytest.fixture
def four_idle_nodes() -> dict[str, dict[int, dict[str, Any]]]:
    return {
        "probe": answered([1, 2, 3, 4], output=probe_output()),
        "claim": answered([1, 2, 3, 4]),
        "start": {
            node: {
                "Status": "Success",
                "StandardOutputContent": started_output(node),
                "StandardErrorContent": "",
            }
            for node in (1, 2, 3, 4)
        },
    }


def test_the_happy_path_claims_then_starts_and_touches_nothing_else(
    monkeypatch: pytest.MonkeyPatch, four_idle_nodes: dict[str, dict[int, dict[str, Any]]]
) -> None:
    cli = FakeCli(fleet=[1, 2, 3, 4], phases=four_idle_nodes)
    monkeypatch.setattr(block_run_distributed, "aws_json", cli)

    done = block_run_distributed.launch(arguments("--node-count", "4"))

    assert done.code == 0
    assert done.plan.mesh.world_size == 32
    assert done.fabric == {1: "tcp", 2: "tcp", 3: "tcp", 4: "tcp"}
    assert not cli.reached("rollback")


def test_the_claim_is_taken_before_anything_is_cloned(
    monkeypatch: pytest.MonkeyPatch, four_idle_nodes: dict[str, dict[int, dict[str, Any]]]
) -> None:
    """Mutation: send the launch script and let it take the claim on the way past.

    That is one call rather than two and it is the partial-claim bug written down: by the time
    the sixth node refuses, five trees have been cloned and five containers are up.
    """
    cli = FakeCli(fleet=[1, 2, 3, 4], phases=four_idle_nodes)
    monkeypatch.setattr(block_run_distributed, "aws_json", cli)

    block_run_distributed.launch(arguments("--node-count", "4"))
    order = [
        cli.phase_of(argv[argv.index("--comment") + 1])
        for argv in cli.calls
        if argv[:2] == ["ssm", "send-command"]
    ]

    assert order == ["probe", "claim", "start"]
    assert "edullm-node claim" in cli.commands["claim"]


def test_a_claim_that_comes_back_short_gives_back_the_ones_it_took(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE STATE THIS WHOLE TOOL EXISTS TO MAKE UNREACHABLE.

    Three machines granted the claim and one refused. Without the release below, those three
    are locked for a run that never started -- and the person who dispatched it has no way to
    tell them apart from three machines somebody is using, because a claim is a claim.
    """
    cli = FakeCli(
        fleet=[1, 2, 3, 4],
        phases={
            "probe": answered([1, 2, 3, 4], output=probe_output()),
            "claim": {
                **answered([1, 2, 3]),
                4: {
                    "Status": "Failed",
                    "StandardOutputContent": "",
                    "StandardErrorContent": "node 4 is held by eric for curriculum-b",
                },
            },
            "rollback": answered([1, 2, 3]),
        },
    )
    monkeypatch.setattr(block_run_distributed, "aws_json", cli)

    done = block_run_distributed.launch(arguments("--node-count", "4"))

    assert done.code == 1
    assert cli.instance_ids_for("rollback") == [
        "i-00000000000000001",
        "i-00000000000000002",
        "i-00000000000000003",
    ]
    assert not cli.reached("start")


def test_a_rollback_after_a_refused_claim_removes_no_container(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing has been started at that point, and ``docker rm --force`` on a name that is not
    there is noise in a CloudTrail record whose whole value is being short enough to read."""
    cli = FakeCli(
        fleet=[1, 2],
        phases={
            "probe": answered([1, 2], output=probe_output()),
            "claim": {**answered([1]), 2: {"Status": "Failed", "StandardErrorContent": "held"}},
            "rollback": answered([1]),
        },
    )
    monkeypatch.setattr(block_run_distributed, "aws_json", cli)

    block_run_distributed.launch(arguments("--node-count", "2"))

    assert "docker rm" not in cli.commands["rollback"]
    assert "edullm-node release --force" in cli.commands["rollback"]


def test_a_rollback_gives_back_only_a_claim_that_names_this_run(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: release unconditionally, which is what ``edullm-node release --force`` does.

    ``--force`` skips the claim phase, so a forced launch that then fails would clear the claim
    of the person whose machine it took -- ending their lock while their container keeps its
    cards, which is worse than either half on its own. The same guard covers the race where
    somebody claims a node in the seconds between a failed start and this rollback.
    """
    cli = FakeCli(
        fleet=[1, 2],
        phases={
            "probe": answered([1, 2], output=probe_output(run="curriculum-b", busy=8)),
            "start": {
                1: {"Status": "Failed", "StandardErrorContent": "no space left on device"},
                2: {"Status": "Success", "StandardOutputContent": started_output(2)},
            },
            "rollback": answered([1, 2]),
        },
    )
    monkeypatch.setattr(block_run_distributed, "aws_json", cli)

    block_run_distributed.launch(arguments("--node-count", "2", "--force"))
    rollback = json.loads(cli.commands["rollback"])["commands"][0]

    assert '[ "$held" = final-model-a ]' in rollback
    assert "/var/lib/edullm/claim.json" in rollback


def test_a_start_that_comes_back_short_removes_every_container_and_every_claim(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE OTHER HALF OF FAILING AS A UNIT, AND IT IS THE HALF THAT COSTS CARDS.

    Three containers up and one node that could not clone. The three are sitting at a
    rendezvous that needs a fourth member and is never going to get one, and torchrun will wait
    out its join timeout before any of them says anything. Removing them is not tidiness: it is
    the difference between the fleet being available again now and being available in a quarter
    of an hour.
    """
    cli = FakeCli(
        fleet=[1, 2, 3, 4],
        phases={
            "probe": answered([1, 2, 3, 4], output=probe_output()),
            "claim": answered([1, 2, 3, 4]),
            "start": {
                **{
                    node: {
                        "Status": "Success",
                        "StandardOutputContent": started_output(node),
                        "StandardErrorContent": "",
                    }
                    for node in (1, 2, 3)
                },
                4: {
                    "Status": "Failed",
                    "StandardOutputContent": "",
                    "StandardErrorContent": "fatal: could not read from remote repository",
                },
            },
            "rollback": answered([1, 2, 3, 4]),
        },
    )
    monkeypatch.setattr(block_run_distributed, "aws_json", cli)

    done = block_run_distributed.launch(arguments("--node-count", "4"))

    assert done.code == 1
    assert cli.instance_ids_for("rollback") == [f"i-{node:017d}" for node in (1, 2, 3, 4)]
    assert "docker rm --force edullm-final-model-a" in cli.commands["rollback"]


def test_a_node_that_never_answered_the_start_is_rolled_back_like_one_that_failed(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: read the invocations that came back and treat the rest as fine.

    Systems Manager returns an entry per machine it reached, so a machine it did not reach is
    absent rather than failed. The job cannot form without it either way.
    """
    cli = FakeCli(
        fleet=[1, 2],
        phases={
            "probe": answered([1, 2], output=probe_output()),
            "claim": answered([1, 2]),
            "start": {
                1: {
                    "Status": "Success",
                    "StandardOutputContent": started_output(1),
                    "StandardErrorContent": "",
                }
            },
            "rollback": answered([1, 2]),
        },
    )
    monkeypatch.setattr(block_run_distributed, "aws_json", cli)

    done = block_run_distributed.launch(arguments("--node-count", "2"))

    assert done.code == 1
    assert cli.reached("rollback")


def test_a_refused_plan_claims_nothing_and_sends_nothing_after_the_probe(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = FakeCli(
        fleet=[1, 2],
        phases={"probe": answered([1, 2], output=probe_output(run="curriculum-b", busy=8))},
    )
    monkeypatch.setattr(block_run_distributed, "aws_json", cli)

    done = block_run_distributed.launch(arguments("--node-count", "2"))

    assert done.code == 1
    assert not cli.reached("claim")
    assert not cli.reached("rollback")


def test_a_dry_run_reads_the_fleet_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, four_idle_nodes: dict[str, dict[int, dict[str, Any]]]
) -> None:
    """The rehearsal to do before the real dispatch, which is the only rehearsal this lane
    gets: the fleet exists for one window and nothing about it can be tried twice."""
    cli = FakeCli(fleet=[1, 2, 3, 4], phases=four_idle_nodes)
    monkeypatch.setattr(block_run_distributed, "aws_json", cli)

    done = block_run_distributed.launch(arguments("--node-count", "4", "--dry-run"))

    assert done.code == 0
    assert done.plan.usable
    assert not cli.reached("claim")
    assert not cli.reached("start")


def test_forcing_skips_the_claim_phase_rather_than_softening_it(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """``edullm-node claim`` has no override, by design, so passing one through would be
    inventing a second claim semantics. The launch script's own write is what takes the node,
    and the consequence -- the other run keeps its cards and the two fight for memory -- is the
    documented meaning of forcing rather than a surprise."""
    cli = FakeCli(
        fleet=[1, 2],
        phases={
            "probe": answered([1, 2], output=probe_output(run="curriculum-b", busy=8)),
            "start": {
                node: {
                    "Status": "Success",
                    "StandardOutputContent": started_output(node),
                    "StandardErrorContent": "",
                }
                for node in (1, 2)
            },
        },
    )
    monkeypatch.setattr(block_run_distributed, "aws_json", cli)

    done = block_run_distributed.launch(arguments("--node-count", "2", "--force"))

    assert done.code == 0
    assert not cli.reached("claim")


def test_every_phase_after_the_probe_names_its_machines_rather_than_a_tag(
    monkeypatch: pytest.MonkeyPatch, four_idle_nodes: dict[str, dict[int, dict[str, Any]]]
) -> None:
    """THE OPPOSITE OF WHAT THE READING TOOLS DO, AND FOR THE OPPOSITE REASON.

    They target a tag so that one unregistered agent does not cost the reading of the whole
    fleet. Here the set is decided and a machine missing from it is a job that cannot form, so
    ``InvalidInstanceId`` on the way in is a better failure than a launch that starts on three
    of the four nodes it was told to use.
    """
    cli = FakeCli(fleet=[1, 2, 3, 4], phases=four_idle_nodes)
    monkeypatch.setattr(block_run_distributed, "aws_json", cli)

    block_run_distributed.launch(arguments("--nodes", "1,2,3"))

    for argv in cli.calls:
        if argv[:2] == ["ssm", "send-command"]:
            assert "--targets" not in argv
    assert cli.instance_ids_for("start") == [f"i-{node:017d}" for node in (1, 2, 3)]


def test_the_launch_command_reaching_the_nodes_is_one_string_for_all_of_them(
    monkeypatch: pytest.MonkeyPatch, four_idle_nodes: dict[str, dict[int, dict[str, Any]]]
) -> None:
    """One ``send-command`` carrying one script, which is only possible because the rendezvous
    form of torchrun leaves nothing per-node to get wrong."""
    cli = FakeCli(fleet=[1, 2, 3, 4], phases=four_idle_nodes)
    monkeypatch.setattr(block_run_distributed, "aws_json", cli)

    block_run_distributed.launch(arguments("--node-count", "4"))
    sent = json.loads(cli.commands["start"])["commands"]

    assert len(sent) == 1
    assert "EDULLM_DIST_RENDEZVOUS_HOST=172.31.0.1:29400" in sent[0]
    assert "EDULLM_DIST_OUTPUT_NODE=1" in sent[0]
    assert LAUNCH_SCRIPT.read_text(encoding="utf-8") in sent[0]


# ---------------------------------------------------------------------------------------
# The two halves that have to agree with each other and with the machine.
# ---------------------------------------------------------------------------------------


def test_the_tool_supplies_every_setting_the_launch_script_refuses_to_start_without() -> None:
    """Mutation: add a ``${EDULLM_DIST_NEW_THING:?}`` to the script and not to the tool.

    The script runs unattended on eight machines at once. An unset setting fails at its first
    line, which is before the claim is rewritten and before anything is cloned -- so the
    symptom is eight identical failures naming a variable, at the moment the flagship run was
    supposed to start. Reading the demands out of the script rather than listing them here is
    what makes the two sides impossible to drift apart.
    """
    demanded = set(REQUIRED_SETTING.findall(LAUNCH_SCRIPT.read_text(encoding="utf-8")))
    supplied = set(
        block_run_distributed.node_settings(
            plan=_usable_plan(),
            who="philote-dev",
            repository="edu-llm/OLMo-core",
            branch="edullm/final-model",
            wandb_project="capacity-block",
            fabric="auto",
        )
    )

    assert demanded, "the script demands nothing, so this test is guarding nothing"
    assert demanded <= supplied, f"the tool never sets {sorted(demanded - supplied)}"


def test_the_prelude_quotes_every_value_it_writes() -> None:
    """A training command carrying a quote, a dollar sign or a pipe passes through a JSON
    document, a shell prelude and a Systems Manager parameter. One unquoted value anywhere in
    that chain is a node running something nobody wrote."""
    written = block_run_distributed.prelude(
        {"EDULLM_DIST_RUN": "a b", "EDULLM_DIST_WHO": "it's; rm -rf /"}
    )

    assert "EDULLM_DIST_RUN='a b'" in written
    assert "rm -rf" in written
    assert written.splitlines()[2].startswith("EDULLM_DIST_WHO='")


def test_the_payload_asks_for_bash_on_its_first_line() -> None:
    """THE ONE THAT MADE EVERY NODE FAIL IDENTICALLY BEFORE A CLAIM WAS EVEN REWRITTEN.

    Systems Manager writes an ``AWS-RunShellScript`` payload to a file and executes it. It
    honours a ``#!`` on line one and uses ``/bin/sh`` -- ``dash`` on this AMI family -- when
    there is not one. ``infra/block-distributed-launch.sh`` has its own shebang and it is not
    on line one of what is sent, because the settings go in front of it, so without this the
    whole launch reaches ``dash`` and dies on ``set -o pipefail`` at the script's thirty-second
    line. Every node answers ``Illegal option -o pipefail`` and nothing else, and the flagship
    run of the window does not start.

    Mutation: drop the line, or move it below the settings, where it is a comment.
    """
    written = block_run_distributed.prelude({"EDULLM_DIST_RUN": "a-run"})

    assert written.splitlines()[0] == "#!/bin/bash"


@pytest.mark.slow
def test_the_launch_payload_runs_under_the_shell_it_asks_for(
    monkeypatch: pytest.MonkeyPatch, four_idle_nodes: dict[str, dict[int, dict[str, Any]]]
) -> None:
    """The whole payload, parsed by the interpreter its first line names.

    ``bash -n`` over the launch script on its own already passes and always did; what broke was
    the composition, where the shebang ends up in the middle. This checks the string that
    actually reaches a node, and it reads the interpreter out of that string rather than
    naming one, so a payload that asks for something it is not is caught here.
    """
    cli = FakeCli(fleet=[1, 2, 3, 4], phases=four_idle_nodes)
    monkeypatch.setattr(block_run_distributed, "aws_json", cli)

    block_run_distributed.launch(arguments("--node-count", "4"))
    payload = json.loads(cli.commands["start"])["commands"][0]
    written = Path(tempfile.mkdtemp()) / "start.sh"
    written.write_text(payload, encoding="utf-8")
    interpreter = payload.splitlines()[0].removeprefix("#!")

    checked = subprocess.run(
        [*interpreter.split(), "-n", str(written)], check=False, capture_output=True, text=True
    )

    assert checked.returncode == 0, checked.stderr


def test_the_claim_the_launch_script_writes_has_the_fields_the_helper_writes() -> None:
    """THE SEAM NEITHER FILE CAN SEE, AND EVERY READER OF A CLAIM IS ON THE OTHER SIDE OF IT.

    ``edullm-node status``, ``tools/block_status.py``, the drain report and the record the
    drain leaves in S3 all read the claim file with a ``sed`` expression per field. A
    distributed run that wrote a claim with different keys would read on every one of those
    surfaces as a machine with nobody on it -- busy cards, no claim, which is the reading that
    invites somebody to start a second run on the same eight.
    """
    def fields(source: str) -> set[tuple[str, ...]]:
        return {
            tuple(re.findall(r'"([a-z_]+)":"%s"', record))
            for record in CLAIM_RECORD.findall(source)
        }

    helper = fields(BOOTSTRAP.read_text(encoding="utf-8"))
    written = fields(LAUNCH_SCRIPT.read_text(encoding="utf-8"))

    assert len(written) == 1, "the launch script writes more than one claim shape"
    assert written == helper


@pytest.mark.slow
def test_the_rollback_command_parses_as_bash(monkeypatch: pytest.MonkeyPatch) -> None:
    """It is the one command in this tool assembled in Python rather than read off a file, and
    it is also the one that runs when something has already gone wrong. A quoting mistake in it
    would present as a rollback that reported success and released nothing.

    **IT IS CHECKED AGAINST ``sh`` AS WELL, WHICH IS THE SHELL IT ACTUALLY GETS.** This one
    carries no shebang -- it is three statements rather than a script -- so Systems Manager
    runs it under ``/bin/sh``, and that is ``dash``. It is POSIX today. A bashism added to it
    would pass ``bash -n``, reach the nodes at the moment a launch has already failed, and take
    the rollback down with it, which is the failure this whole path exists to prevent.
    """
    cli = FakeCli(
        fleet=[1, 2],
        phases={
            "probe": answered([1, 2], output=probe_output()),
            "claim": answered([1, 2]),
            "start": {1: {"Status": "Failed", "StandardErrorContent": "boom"}},
            "rollback": answered([1, 2]),
        },
    )
    monkeypatch.setattr(block_run_distributed, "aws_json", cli)

    block_run_distributed.launch(arguments("--node-count", "2"))
    written = Path(tempfile.mkdtemp()) / "rollback.sh"
    written.write_text(json.loads(cli.commands["rollback"])["commands"][0] + "\n", "utf-8")

    for shell in ("bash", "sh"):
        checked = subprocess.run(
            [shell, "-n", str(written)], check=False, capture_output=True, text=True
        )

        assert checked.returncode == 0, f"{shell}: {checked.stderr}"


def test_the_fabric_each_node_chose_is_read_out_of_what_it_printed() -> None:
    found = block_run_distributed._fabric_of(
        block_run_distributed.outcomes(
            _usable_plan().chosen,
            {
                "i-00000000000000001": {
                    "Status": "Success",
                    "StandardOutputContent": started_output(1, fabric="efa"),
                },
                "i-00000000000000002": {
                    "Status": "Success",
                    "StandardOutputContent": started_output(2, fabric="tcp"),
                },
            },
        )
    )

    assert found == {1: "efa", 2: "tcp"}


def _usable_plan() -> Any:
    from edullm_platform.block_fleet import FleetNode, parse_reading
    from edullm_platform.block_multinode import plan_launch

    fleet = tuple(
        FleetNode(
            node=number,
            instance_id=f"i-{number:017d}",
            state="running",
            private_ip=f"172.31.0.{number}",
            capacity_reservation_id=BLOCK,
        )
        for number in (1, 2)
    )
    readings = tuple(
        parse_reading(
            node=number,
            instance_id=f"i-{number:017d}",
            status="Success",
            output=probe_output(),
        )
        for number in (1, 2)
    )
    return plan_launch(
        fleet=fleet, readings=readings, run="final-model-a", command=TRAINING, node_count=2
    )
