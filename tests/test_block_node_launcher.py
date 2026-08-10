"""``edullm-node run`` refusing a command that cannot start, run rather than read.

The workflow's copy of this guard is the one that protects the button, and it is the one that
reaches the fleet in a live window -- ``infra/block-node-bootstrap.sh`` is user-data, it runs
once at launch, and nothing re-runs it, so what is written here reaches machines built after it
merges and no machine that is already up. What this copy is for is the other door: somebody
sitting in a shell on a node, typing the verb directly, with no workflow between them and the
eight cards.

**IT IS DELIBERATELY NARROWER THAN ``edullm_platform.block_launcher`` AND THE REASON IS A BYTE
BUDGET RATHER THAN AN OVERSIGHT.** EC2 refuses user-data above 16,384 bytes compressed, and
this file was already at 14,903 of that before the guard -- ``block-launch-fleet.yml`` carries
its own refusal for the same limit, and the remedy its message names is moving the bootstrap to
S3, which is a change this is not. So the node checks the model factory and nothing else: no
mesh flags, no shell-wrapper parsing, no spliced correction. The tests below hold that line
explicitly rather than leaving somebody to infer it from what is missing.

**THESE TESTS RUN THE HELPER RATHER THAN READING IT**, which is the argument
``tests/test_block_node_claim.py`` makes at length about the same file. The bootstrap installs
several hundred lines of shell through a quoted heredoc, where ``bash -n`` over the outer file
sees literal text and an assertion that the word ``olmoe_7b_32x4`` appears would pass against a
guard that fires on the wrong branch, prints the wrong number, or takes the machine down with
it. So the helper is extracted, pointed at a settings file and a PATH of stubs, and asked what
it does when it is handed the command that killed two runs on 2026-08-10.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_PATH = PROJECT_ROOT / "infra" / "block-node-bootstrap.sh"

#: The helper, out of the heredoc the bootstrap installs it with. Anchored on that exact path so
#: that a rename of the installed command fails here rather than silently testing nothing.
HELPER_BODY = re.compile(
    r"^cat > /usr/local/bin/edullm-node <<'(?P<delimiter>[A-Z]+)'\n(?P<body>.*?)\n(?P=delimiter)\n",
    re.MULTILINE | re.DOTALL,
)

#: The one absolute path the helper reads that a test cannot write.
SETTINGS_LINE = ". /etc/edullm-block.env"

#: The command from ``.edullm/run.yaml`` on ``edu-llm/OLMo-core@edullm/final-model``, shortened
#: to the flags this guard reads. It carries no launcher, which is correct in that file and
#: fatal through this verb.
COMMITTED = (
    "python .edullm/train_on_corpus.py --model-factory olmoe_7b_32x4 "
    "--dataset-id pretrain/reservoir-dolma2 --steps 11921"
)

#: Eight cards, none of them busy, which is an idle p5 node. ``total_gpus`` counts the lines of
#: the first query and ``busy_gpus`` the unique lines of the second, so the two are answered
#: separately -- a stub that answered both the same way would report the node fully occupied.
NVIDIA_SMI_STUB = """
for argument in "$@"; do
  case "${argument}" in
    --query-compute-apps=*) exit 0 ;;
    --query-gpu=uuid) seq 1 8 | sed 's/^/GPU-0000000/' ; exit 0 ;;
  esac
done
exit 0
"""

#: A ``git`` that clones into a tree with nothing in it. Every test here passes ``--command``,
#: so the helper never looks for ``.edullm/run.yaml`` and the clone only has to succeed.
GIT_CLONES = """
if [ "${1:-}" = clone ]; then
  target=""
  for target; do :; done
  mkdir -p "${target}"
  exit 0
fi
if [ "${1:-}" = -C ]; then
  echo 4a26f9d0d1cf9b2a3e5c7181b0d4f6a8c2e10b73
  exit 0
fi
exit 0
"""

#: A ``docker`` whose ``ps`` answers out of a marker its own ``run`` writes, so "did a container
#: start" is a consequence of the helper having reached ``docker run`` rather than a setting.
DOCKER_STUB = """
if [ "${1:-}" = ps ]; then
  [ -f "${DOCKER_MARKER}" ] && echo c0ffee1234
  exit 0
fi
if [ "${1:-}" = run ]; then
  : > "${DOCKER_MARKER}"
  echo c0ffee1234
  exit 0
fi
exit 0
"""

STUBS = {
    "aws": 'echo "a-weights-and-biases-key"\nexit 0\n',
    "git": GIT_CLONES,
    "nvidia-smi": NVIDIA_SMI_STUB,
    "python3": 'echo "python train.py"\nexit 0\n',
}


def _write_stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/usr/bin/env bash\n{body}", encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def node(tmp_path: Path) -> dict[str, Path]:
    """One capacity block node with eight idle cards, as far as the helper can tell.

    The stub directory is prepended to PATH rather than replacing it: the helper reaches for
    ``sed``, ``date``, ``rm`` and ``mkdir`` as well, and a PATH holding only stubs would fail
    every test here for a reason that has nothing to do with launchers.
    """
    match = HELPER_BODY.search(BOOTSTRAP_PATH.read_text(encoding="utf-8"))
    assert match is not None, "the bootstrap no longer installs /usr/local/bin/edullm-node"

    state = tmp_path / "state"
    scratch = tmp_path / "scratch"
    binaries = tmp_path / "bin"
    for directory in (state, scratch, binaries):
        directory.mkdir()

    settings = tmp_path / "edullm-block.env"
    settings.write_text(
        "\n".join(
            [
                "EDULLM_BLOCK_RESERVATION=cr-0000000000000000a",
                "EDULLM_BLOCK_NODE=3",
                "EDULLM_BLOCK_OUTPUTS_BUCKET=edullm-block-outputs-us-east-2",
                "EDULLM_BLOCK_DATA_BUCKET=edullm-data-us-east-2",
                "EDULLM_BLOCK_IMAGE=registry/olmo-core:abc123",
                "EDULLM_BLOCK_IMAGE_REGION=us-east-1",
                "EDULLM_BLOCK_REGION=us-east-2",
                "EDULLM_BLOCK_WANDB_SECRET_ID=a-secret",
                "EDULLM_BLOCK_LOG_SYNC_SECONDS=60",
                "EDULLM_BLOCK_S3_PREFIX=block/cr-0000000000000000a/node-3",
                f"EDULLM_BLOCK_SCRATCH={scratch}",
                f"EDULLM_BLOCK_STATE={state}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    body = match.group("body")
    assert SETTINGS_LINE in body, "the helper no longer reads /etc/edullm-block.env"
    helper = tmp_path / "edullm-node"
    helper.write_text(body.replace(SETTINGS_LINE, f'. "{settings}"'), encoding="utf-8")
    helper.chmod(0o755)

    for name, stub in STUBS.items():
        _write_stub(binaries, name, stub)
    _write_stub(binaries, "docker", DOCKER_STUB)

    return {
        "helper": helper,
        "claim": state / "claim.json",
        "binaries": binaries,
        "marker": tmp_path / "container-is-up",
    }


def start(node: dict[str, Path], command: str) -> subprocess.CompletedProcess[str]:
    """``edullm-node run`` with this command, on a node nobody is holding."""
    return subprocess.run(
        [
            str(node["helper"]),
            "run",
            "--name",
            "a-probe",
            "--branch",
            "edullm/final-model",
            "--who",
            "ana",
            "--command",
            command,
        ],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{node['binaries']}{os.pathsep}{os.environ['PATH']}",
            "DOCKER_MARKER": str(node["marker"]),
        },
    )


def test_the_command_that_killed_two_runs_never_reaches_docker(node: dict[str, Path]) -> None:
    """Mutation: run whatever arrives, which is what this verb did until now.

    One process of a 64-rank recipe on a machine with eight cards holds one of them, bills for
    all of them, and stops on a message about the parallelism mesh -- which is a mesh that could
    not be built out of one rank rather than a mesh anybody got wrong.
    """
    done = start(node, COMMITTED)

    assert done.returncode != 0
    assert "command_needs_a_launcher:olmoe_7b_32x4" in done.stderr
    assert "parallelism mesh rather than about the launcher" in done.stderr
    assert not node["marker"].exists(), "the refusal still started a container"


def test_a_refused_command_gives_the_node_back(node: dict[str, Path]) -> None:
    """THE HALF OF THIS THAT IS ABOUT THE FLEET RATHER THAN ABOUT THE RUN.

    The claim is written before the clone, so every refusal after that point has to release it
    or the node reads as busy to ``block-run.yml``, to the status tool and to everybody looking
    at the sheet -- for a run that does not exist, until somebody who has heard of ``release``
    finds it. A guard that costs a machine out of eight is worse than the defect it prevents.
    """
    done = start(node, COMMITTED)

    assert done.returncode != 0
    assert not node["claim"].exists()


def test_the_same_command_under_a_launcher_starts(node: dict[str, Path]) -> None:
    """The other half of the rule. Nothing here judges the rank count -- the container is given
    every card and this verb has no shape to compare against -- so a launcher is the whole of
    what it asks for."""
    done = start(
        node,
        COMMITTED.replace(
            "python .edullm/",
            "python -m torch.distributed.run --nproc-per-node=8 --standalone .edullm/",
            1,
        ),
    )

    assert done.returncode == 0, done.stderr
    assert node["marker"].exists()
    assert node["claim"].exists()


@pytest.mark.parametrize(
    "command",
    [
        "nvidia-smi",
        "python -c 'import torch; print(torch.cuda.device_count())'",
        "bash -lc 'df -h /scratch && nvidia-smi -L'",
        "python .edullm/train_on_corpus.py --model-factory olmo2_1B --steps 10",
    ],
)
def test_ordinary_single_process_work_still_starts(node: dict[str, Path], command: str) -> None:
    """The dispatches this button is used for between training runs, every one of them one
    process on a machine with eight cards on purpose. A guard that refused these would be
    routed around rather than read, which is how a control stops working."""
    done = start(node, command)

    assert done.returncode == 0, done.stderr
    assert node["marker"].exists()


def test_the_waiver_starts_it_anyway(node: dict[str, Path]) -> None:
    """Somebody reproducing the failure on purpose, or holding one card of eight deliberately.
    The token is the one the submission path already uses, so nobody has to learn a second
    spelling for the same sentence."""
    done = start(node, f"EDULLM_LAUNCH_CHECK=waived {COMMITTED}")

    assert done.returncode == 0, done.stderr
    assert node["marker"].exists()


def test_the_refusal_names_the_cards_it_counted_rather_than_a_number_from_memory(
    node: dict[str, Path],
) -> None:
    """THE ONE THING THIS SURFACE KNOWS THAT THE WORKFLOW DOES NOT.

    ``block-run.yml`` refuses before any machine has been addressed and writes torchrun's own
    ``gpu`` into the remedy for that reason. Here ``nvidia-smi`` has already answered, so the
    line somebody pastes carries the figure this node actually reports -- and a fleet on a
    different shape would get a different one without anybody editing this file.
    """
    done = start(node, COMMITTED)

    assert "--nproc-per-node=8" in done.stderr
    assert "--nproc-per-node=gpu" not in done.stderr


def test_the_refusal_says_not_to_repair_the_committed_file(node: dict[str, Path]) -> None:
    """The obvious response to "your command has no launcher" is to put one where the command
    came from, and ``.edullm/run.yaml`` is read by two paths: this one runs it as written, and
    ``block-run-distributed.yml`` prepends the rendezvous form. A launcher committed there
    fixes this verb and gives the eight-node dispatch sixty-four workers over eight cards."""
    done = start(node, COMMITTED)

    assert "Leave .edullm/run.yaml alone" in done.stderr
    assert "block-run-distributed.yml" in done.stderr
    assert "64 workers over 8 cards" in done.stderr


def test_the_node_checks_the_factory_and_leaves_the_mesh_flags_to_the_workflow(
    node: dict[str, Path],
) -> None:
    """THE NARROWING, KEPT AS A PASSING TEST SO THAT IT READS AS A DECISION RATHER THAN A GAP.

    A mesh named on the command is evidence to ``block_launcher`` and is not checked here. It
    could be, and what it would cost is bytes out of a user-data budget with 924 of 16,384 left
    -- against a signal that catches a line pasted out of the distributed lane, which is the
    rarer half of the defect and the half the button already refuses.
    """
    done = start(node, "python .edullm/train_on_corpus.py --moe-shard-degree 8")

    assert done.returncode == 0, done.stderr
    assert node["marker"].exists()
