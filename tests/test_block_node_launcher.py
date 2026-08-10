"""``edullm-node run`` and the launcher check that is deliberately not on the node.

**THERE WAS A COPY OF THE GUARD HERE AND IT WAS TAKEN OUT TO LAUNCH THE FLEET.** It cost 747
bytes of a user-data budget with 224 left once the three branches merged for the 2026-08-10
window were put together, and EC2 refuses user-data above 16,384 bytes compressed. Over that
number no node boots at all, so the choice was between one duplicated check and the eight-node
launch. ``test_block_workflows.py`` measures the file on every pull request and the numbers are
in the pull request that made this change.

**WHAT WAS LOST IS SMALLER THAN IT LOOKS, AND NAMING IT IS THE POINT OF THIS FILE.**
``block-run.yml`` refuses the same command before the credentials and before any node is
addressed, and :mod:`edullm_platform.block_launcher` holds the full reading and the remedy --
so the button that fifteen of thirty-five people use is covered, and covered better, because
the workflow can read ``.edullm/run.yaml`` off the branch and this verb only ever sees what it
was handed. Three things follow from the node copy being gone, and all three are asserted
below rather than left to be discovered:

*  ``edullm-node run`` typed in a shell on a node will start a 64-rank command as one process.
   That door belongs to the twenty people who hold an AWS role, which is the population that
   already knows, and it is the door the workflow was always in front of.
*  A node-side guard reaches no machine that is already running, because nothing re-runs
   user-data. The fleet this window was bought for was already up when the guard was written,
   so its value during that window was zero whichever way the merge went.
*  A workflow refusal takes effect the moment it is on ``main``. That asymmetry is the whole
   argument, and it is why the workflow copy is the one that survived.

**THESE TESTS RUN THE HELPER RATHER THAN READING IT**, which is the argument
``tests/test_block_node_claim.py`` makes at length about the same file. The bootstrap installs
several hundred lines of shell through a quoted heredoc, where ``bash -n`` over the outer file
sees literal text -- so an assertion about what the node does or does not refuse is only worth
having if something executes it. The helper is extracted, pointed at a settings file and a PATH
of stubs, and asked what it does with the command that killed two runs on 2026-08-10.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from edullm_platform.block_launcher import launcher_refusals

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


def test_the_node_starts_the_command_that_killed_two_runs_and_that_is_the_exposure(
    node: dict[str, Path],
) -> None:
    """THE COST OF THE BYTE BUDGET, WRITTEN AS A PASSING TEST SO THAT IT CANNOT BE FORGOTTEN.

    This is not an assertion that the behaviour is right. It is an assertion about what this
    surface does, so that the residual exposure has a name and a location instead of living in
    a pull request nobody reads again: typed in a shell, this verb will start a 64-rank recipe
    as one process on eight cards and nothing here will stop it.

    Restoring the guard is a legitimate thing to want. It is not free, and the sibling test
    below holds the number it costs. The order to try things in is in the pull request: shrink
    the file, move the bootstrap to S3 and fetch it from a stub -- which is what
    ``test_block_workflows.py`` has recommended in its own refusal message all along -- and only
    then spend the bytes here.
    """
    done = start(node, COMMITTED)

    assert done.returncode == 0, done.stderr
    assert node["marker"].exists(), "the node refused a command the workflow is what refuses"
    assert "command_needs_a_launcher" not in done.stderr


def test_the_bootstrap_carries_no_launcher_check_and_says_where_it_went(
    node: dict[str, Path],
) -> None:
    """Mutation: put the guard back without reading what it costs.

    A reader who finds the note in ``do_run`` and deletes it, or who writes a fresh check beside
    it, gets a fleet that does not launch -- ``block-launch-fleet.yml`` refuses over the limit
    and ``run-instances`` refuses under it. Failing here instead is several hours earlier and
    several thousand dollars cheaper, and the note is what sends them to the reasoning.
    """
    bootstrap = BOOTSTRAP_PATH.read_text(encoding="utf-8")

    assert "command_needs_a_launcher" not in bootstrap
    assert "olmoe_7b_32x4" not in bootstrap
    assert "NO LAUNCHER CHECK HERE, DELIBERATELY" in bootstrap
    assert Path(__file__).name in bootstrap, "the note points at no reasoning"


def test_the_workflow_refuses_what_the_node_no_longer_does() -> None:
    """THE CHECK THAT SURVIVED, ASSERTED FROM THE FILE THAT LOST ITS COPY.

    The two are one decision and reading either alone gets it wrong, so the test for the half
    that was dropped is the test that pins the half that was kept. If somebody removes the
    workflow refusal, the argument in this file's header stops being true and this fails --
    which is the failure that matters, because at that point nothing anywhere refuses it.
    """
    workflow = (
        PROJECT_ROOT / ".github" / "workflows" / "block-run.yml"
    ).read_text(encoding="utf-8")

    assert "from edullm_platform.block_launcher import launcher_refusals" in workflow
    assert "launcher_refusals(command)" in workflow
    assert launcher_refusals(COMMITTED), "the module the workflow calls refuses nothing"


def test_the_same_command_under_a_launcher_starts(node: dict[str, Path]) -> None:
    """THE SHAPE THE DISTRIBUTED LANE SENDS, WHICH IS THE ONE THAT MUST NEVER BE REFUSED HERE.

    ``block-run-distributed.yml`` prepends the rendezvous form itself and hands the composed
    line to this verb, so what arrives on a node during the eight-node run already carries a
    launcher. It kept working when the guard was here, it keeps working now, and
    ``test_block_launcher.py`` holds the same question against the workflow copy.
    """
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


def test_the_waiver_is_carried_through_rather_than_read_here(node: dict[str, Path]) -> None:
    """The token the workflow reads, passed down a verb that no longer has an opinion on it.

    ``EDULLM_LAUNCH_CHECK=waived`` is how somebody records that one process was the point, and
    :mod:`edullm_platform.block_launcher` is what honours it now. What matters here is only that
    a command carrying it still runs: an environment assignment in front of a command line is a
    shape ``bash -lc`` accepts, and a node that choked on it would turn the workflow's escape
    hatch into a second failure.
    """
    done = start(node, f"EDULLM_LAUNCH_CHECK=waived {COMMITTED}")

    assert done.returncode == 0, done.stderr
    assert node["marker"].exists()
