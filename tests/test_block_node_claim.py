"""What ``edullm-node run`` leaves behind on a node when the run it was asked for never starts.

**THE CLAIM FILE IS THE LOCK FOR THE WHOLE FLEET, AND IT IS TAKEN BEFORE THE WORK.**
``infra/block-node-bootstrap.sh`` writes it before the clone on purpose: two people dispatching
seconds apart would otherwise both read an unheld node and both proceed, which on a block costs
two runs rather than one. Everything after that point can still fail, and under ``set -e`` each
of those failures used to leave the claim standing. A node claimed for a run that does not exist
reads as busy to ``block-run.yml``, to ``tools/block_status.py`` and to everybody looking at the
sheet, and the only cure is a verb -- ``edullm-node release`` -- that somebody has to already
know about while sitting in a shell they may hold no role to open.

**THE FAILURE THAT MAKES THIS ROUTINE RATHER THAN UNLUCKY IS A MISSING ``.edullm/run.yaml``.**
Only OLMo-core carries one. Every other repository in ``config/repositories.yaml`` reaches this
helper with no command in the tree, so the first dispatch anybody makes from a new codebase is
the one that takes a machine out of a fleet of eight.

**THESE TESTS RUN THE HELPER RATHER THAN READING IT**, which is the whole reason the module is
worth its length. The bootstrap installs several hundred lines of shell through a quoted
heredoc, where ``bash -n`` over the outer file sees literal text; a test asserting that the word
``trap`` appears would pass against a trap that fires on the wrong condition, and the condition
is the entire content of the change. So the helper is extracted, pointed at a settings file and
a PATH of stubs, and asked what it does to the claim.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_PATH = PROJECT_ROOT / "infra" / "block-node-bootstrap.sh"

#: The helper, out of the ``cat > /usr/local/bin/edullm-node <<'HELPER'`` the bootstrap writes it
#: with. Anchored on that exact path so that a rename of the installed command fails here rather
#: than silently testing nothing.
HELPER_BODY = re.compile(
    r"^cat > /usr/local/bin/edullm-node <<'(?P<delimiter>[A-Z]+)'\n(?P<body>.*?)\n(?P=delimiter)\n",
    re.MULTILINE | re.DOTALL,
)

#: The one absolute path the helper reads that a test cannot write. Everything else it touches --
#: the state directory, ``/scratch``, the commands on PATH -- is named by a setting or resolved
#: through PATH, so this substitution is the whole of what makes the script runnable off a node.
SETTINGS_LINE = ". /etc/edullm-block.env"

#: A ``git`` that refuses the clone the way a private repository refuses it: no output on stdout,
#: a message about credentials, and the non-zero status git itself uses.
GIT_REFUSES_THE_CLONE = """
if [ "${1:-}" = clone ]; then
  echo "fatal: could not read Username for 'https://github.com': No such device or address" >&2
  exit 128
fi
exit 0
"""

#: The destination of a clone, which is the last argument. Written as a loop rather than as
#: ``${!#}`` because the bash macOS ships is 3.2, and a stub the suite cannot execute on a
#: maintainer's laptop is a stub that only runs in CI.
LAST_ARGUMENT = 'target=""\nfor target; do :; done\n'

#: A ``git`` that clones. The tree it produces carries no ``.edullm/run.yaml``, which is the
#: state every repository but OLMo-core is in.
GIT_CLONES_A_TREE_WITH_NO_SPEC = f"""
if [ "${{1:-}}" = clone ]; then
  {LAST_ARGUMENT}
  mkdir -p "${{target}}"
  exit 0
fi
if [ "${{1:-}}" = -C ]; then
  echo 4a26f9d0d1cf9b2a3e5c7181b0d4f6a8c2e10b73
  exit 0
fi
exit 0
"""

#: A ``git`` that clones a tree carrying a command, so the run reaches ``docker run``.
GIT_CLONES_A_TREE_WITH_A_SPEC = f"""
if [ "${{1:-}}" = clone ]; then
  {LAST_ARGUMENT}
  mkdir -p "${{target}}/.edullm"
  printf 'command: python train.py\\n' > "${{target}}/.edullm/run.yaml"
  exit 0
fi
if [ "${{1:-}}" = -C ]; then
  echo 4a26f9d0d1cf9b2a3e5c7181b0d4f6a8c2e10b73
  exit 0
fi
exit 0
"""

#: A ``docker`` whose ``ps`` answers out of a marker its own ``run`` writes, so that "is a
#: container up" is a consequence of a run having happened rather than a fixture setting. The
#: marker is per container name, because the helper asks the question about one name while
#: another may be running and the two answers have to differ.
DOCKER_STUB = """
if [ "${1:-}" = ps ]; then
  for argument in "$@"; do
    case "${argument}" in
      name=^edullm-*$)
        wanted="${argument#name=^edullm-}"
        wanted="${wanted%$}"
        ;;
    esac
  done
  if [ -f "${DOCKER_MARKER}-${wanted:-none}" ]; then
    echo c0ffee1234
  fi
  exit 0
fi
if [ "${1:-}" = run ]; then
  for argument in "$@"; do
    case "${argument}" in
      edullm-*) started="${argument#edullm-}" ;;
    esac
  done
  if [ "${DOCKER_RUN_FAILS:-no}" = yes ]; then
    echo "docker: Error response from daemon: no such image" >&2
    exit 125
  fi
  : > "${DOCKER_MARKER}-${started:-none}"
  echo c0ffee1234
  exit 0
fi
exit 0
"""

STUBS = {
    "aws": 'echo "a-weights-and-biases-key"\nexit 0\n',
    "nvidia-smi": "exit 0\n",
    "python3": 'echo "python train.py"\nexit 0\n',
}


def _write_stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/usr/bin/env bash\n{body}", encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def node(tmp_path: Path) -> dict[str, object]:
    """One capacity block node, as far as the helper can tell.

    The stub directory is *prepended* to PATH rather than replacing it. The helper reaches for
    ``sed``, ``date``, ``find``, ``rm`` and ``mkdir`` as well, and a PATH holding only stubs would
    make every test here fail for a reason that has nothing to do with the claim.
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


def _run(
    node: dict[str, object], *arguments: str, git: str, docker_run_fails: bool = False
) -> subprocess.CompletedProcess[str]:
    binaries = node["binaries"]
    assert isinstance(binaries, Path)
    _write_stub(binaries, "git", git)
    return subprocess.run(
        [str(node["helper"]), *arguments],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{binaries}{os.pathsep}{os.environ['PATH']}",
            "DOCKER_MARKER": str(node["marker"]),
            "DOCKER_RUN_FAILS": "yes" if docker_run_fails else "no",
        },
    )


def _claim(node: dict[str, object]) -> Path:
    path = node["claim"]
    assert isinstance(path, Path)
    return path


def test_a_clone_that_is_refused_gives_the_node_back(node: dict[str, object]) -> None:
    """Mutation: take the claim before the clone and never give it back.

    This is the failure a private repository produces, and it produces it *after* the claim,
    seconds into a Systems Manager invocation nobody is watching. Leaving the claim standing
    costs one of eight machines for as long as it takes somebody to work out that a verb exists
    to clear it.
    """
    done = _run(
        node,
        "run",
        "--name",
        "an-eval",
        "--repository",
        "edu-llm/something-private",
        "--branch",
        "edullm/main",
        "--who",
        "ana",
        git=GIT_REFUSES_THE_CLONE,
    )

    assert done.returncode != 0
    assert not _claim(node).exists(), (
        "the clone failed and the node is still claimed for a run that does not exist"
    )


def test_a_refused_clone_says_the_node_holds_no_credential(node: dict[str, object]) -> None:
    """Mutation: let git's own message stand.

    ``could not read Username`` describes a prompt that could not be shown. It says nothing
    about the node holding no GitHub credential by design, and somebody reading it at three in
    the morning reasonably concludes their branch name is wrong.
    """
    done = _run(
        node,
        "run",
        "--name",
        "an-eval",
        "--repository",
        "edu-llm/something-private",
        "--branch",
        "edullm/main",
        git=GIT_REFUSES_THE_CLONE,
    )

    assert "no GitHub credential" in done.stderr
    assert "private repository" in done.stderr


def test_a_repository_carrying_no_run_yaml_gives_the_node_back(node: dict[str, object]) -> None:
    """Mutation: refuse the missing spec without releasing.

    Only OLMo-core carries ``.edullm/run.yaml``. Every other repository reaches this refusal on
    its first dispatch, so a claim leaked here is not an edge case -- it is what onboarding a
    second codebase onto the block does to a node.
    """
    done = _run(
        node,
        "run",
        "--name",
        "post-train-1",
        "--repository",
        "edu-llm/open-instruct-scored-rewards",
        "--branch",
        "main",
        git=GIT_CLONES_A_TREE_WITH_NO_SPEC,
    )

    assert done.returncode != 0
    assert ".edullm/run.yaml" in done.stderr
    assert not _claim(node).exists()


def test_a_container_that_never_started_gives_the_node_back(node: dict[str, object]) -> None:
    """Mutation: release only on a clone failure.

    ``docker run`` refusing is the last thing that can go wrong, and it goes wrong for reasons
    that have nothing to do with the researcher: an image the node could not pull, a daemon that
    is unwell. The claim is worth exactly as little in that case as in the others.
    """
    done = _run(
        node,
        "run",
        "--name",
        "an-arm",
        "--branch",
        "edullm/final-model",
        git=GIT_CLONES_A_TREE_WITH_A_SPEC,
        docker_run_fails=True,
    )

    assert done.returncode != 0
    assert not _claim(node).exists()


def test_a_run_that_did_start_keeps_the_node(node: dict[str, object]) -> None:
    """Mutation: release unconditionally on the way out.

    A trap that does not ask whether a container came up is worse than no trap at all: it hands
    the machine to the next dispatch while sixty-four cards are training on it, and the claim is
    the only thing that was stopping that.
    """
    done = _run(
        node,
        "run",
        "--name",
        "an-arm",
        "--branch",
        "edullm/final-model",
        git=GIT_CLONES_A_TREE_WITH_A_SPEC,
    )

    assert done.returncode == 0, done.stderr
    claim = _claim(node)
    assert claim.exists(), "the run started and the node is not claimed for it"
    assert '"run":"an-arm"' in claim.read_text(encoding="utf-8")
    assert '"commit":"4a26f9d0' in claim.read_text(encoding="utf-8")


def test_a_second_start_of_a_live_run_leaves_its_claim_alone(node: dict[str, object]) -> None:
    """Mutation: arm the release above the check that the name is already running.

    Two dispatches of one run name minutes apart is ordinary -- a workflow re-run, somebody
    pressing the button twice -- and the second is refused because the container is up. That
    refusal happens before the claim is touched, so the release must not be armed yet. Armed
    earlier, the safety net would take the lock off a run that is training on all eight cards.
    """
    started = _run(
        node,
        "run",
        "--name",
        "an-arm",
        "--branch",
        "edullm/final-model",
        git=GIT_CLONES_A_TREE_WITH_A_SPEC,
    )
    assert started.returncode == 0, started.stderr

    again = _run(
        node,
        "run",
        "--name",
        "an-arm",
        "--branch",
        "edullm/final-model",
        git=GIT_CLONES_A_TREE_WITH_A_SPEC,
    )

    assert again.returncode != 0
    assert "already running" in again.stderr
    assert _claim(node).exists(), "a refused second start cleared the claim of the live run"
