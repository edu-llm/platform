"""What ``edullm-node drain`` calls a failure, run rather than read.

**THE DRAIN USED TO REPORT A WHOLE PREFIX AS LOST BECAUSE A SYMLINK POINTED AT NOTHING.**
``aws s3 sync`` exits 2 when it skipped at least one file and transferred every other one, and
exits 1 when a transfer actually failed. ``flush_run`` treated any non-zero as ``failed``.
Weights and Biases leaves ``wandb/latest-run`` and a ``logs/debug-core.log`` behind as dangling
symlinks in every run directory it touches, the CLI warns ``File does not exist`` on each and
exits 2, and the entire prefix is in S3 regardless.

Observed on node 1 of ``cr-05872979e28a491aa`` at 18:13 on 2026-08-10: three runs reported
``block_drain_incomplete ... reported failed`` in a job summary that went on to say ``0 of 561
files are not in S3`` about the same three. The drain is read once, on the last morning of a
window, by people deciding whether their work is safe, and a report that contradicts itself
there is worse than no report -- the thing it teaches is to stop reading it.

**THESE TESTS RUN THE HELPER RATHER THAN READING IT**, for the reason
``tests/test_block_node_claim.py`` gives at length: the helper is installed through a quoted
heredoc, so ``bash -n`` over the outer file sees literal text, and an assertion that the digit
``2`` appears somewhere would pass against a comparison written the wrong way round. So the
helper is extracted, given an ``aws`` that exits the way the real one does, and asked what
verdict it prints.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_PATH = PROJECT_ROOT / "infra" / "block-node-bootstrap.sh"

#: The helper, out of the heredoc the bootstrap installs it with. Anchored on the exact path so
#: that a rename of the installed command fails here rather than silently testing nothing.
HELPER_BODY = re.compile(
    r"^cat > /usr/local/bin/edullm-node <<'(?P<delimiter>[A-Z]+)'\n(?P<body>.*?)\n(?P=delimiter)\n",
    re.MULTILINE | re.DOTALL,
)

SETTINGS_LINE = ". /etc/edullm-block.env"

#: An ``aws`` standing in for the real one. ``s3 sync`` exits with ``AWS_SYNC_EXIT`` and ``s3
#: ls`` prints ``AWS_LS_LINES`` object rows, which is the pair ``flush_run`` reduces to a
#: verdict. The warning goes to stderr because that is where the real CLI puts it, and a test
#: that fed it to stdout would not notice a change that started parsing the sync's output.
AWS_STUB = """
if [ "${1:-}" = s3 ] && [ "${2:-}" = sync ]; then
  if [ "${AWS_SYNC_EXIT:-0}" -eq 2 ]; then
    echo "warning: Skipping file /scratch/r/wandb/latest-run/logs/debug-core.log. File does not exist." >&2
  fi
  exit "${AWS_SYNC_EXIT:-0}"
fi
if [ "${1:-}" = s3 ] && [ "${2:-}" = ls ]; then
  index=0
  while [ "${index}" -lt "${AWS_LS_LINES:-0}" ]; do
    echo "2026-08-10 18:13:46        128 scratch/file-${index}"
    index=$((index + 1))
  done
  exit 0
fi
exit 0
"""

#: A ``date`` that answers the two questions the drain asks it. Stubbed rather than inherited
#: because ``do_drain`` reads ``EDULLM_BLOCK_ENDS_AT`` with ``date -u -d``, which is GNU, and the
#: ``date`` macOS ships has no ``-d`` -- so without this the suite passes only on Linux, which is
#: the half of the time it is least needed. The two epochs are far apart on purpose: the drain
#: subtracts them to decide how much of the window is left, and a negative answer takes a
#: different path through the report than the one under test.
DATE_STUB = """
if [ "${1:-}" = -u ] && [ "${2:-}" = -d ]; then
  echo 4070908800
  exit 0
fi
if [ "${1:-}" = -u ] && [ "${2:-}" = +%s ]; then
  echo 1770000000
  exit 0
fi
echo 2026-08-10T18:13:46Z
exit 0
"""

STUBS = {
    "nvidia-smi": "exit 0\n",
    "docker": "exit 0\n",
    "python3": "exit 0\n",
    "date": DATE_STUB,
}


def _write_stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/usr/bin/env bash\n{body}", encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def node(tmp_path: Path) -> dict[str, object]:
    """One node with one run directory holding two files, as far as the helper can tell."""
    match = HELPER_BODY.search(BOOTSTRAP_PATH.read_text(encoding="utf-8"))
    assert match is not None, "the bootstrap no longer installs /usr/local/bin/edullm-node"

    state = tmp_path / "state"
    scratch = tmp_path / "scratch"
    binaries = tmp_path / "bin"
    for directory in (state, scratch, binaries):
        directory.mkdir()

    run_directory = scratch / "a-run"
    (run_directory / "log").mkdir(parents=True)
    (run_directory / "log" / "train.log").write_text("step 1\n", encoding="utf-8")

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
                "EDULLM_BLOCK_ENDS_AT=2099-01-01T00:00:00Z",
                "EDULLM_BLOCK_RECLAIM_MINUTES=30",
                "EDULLM_BLOCK_DRAIN_FROM_MINUTES=150",
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
    _write_stub(binaries, "aws", AWS_STUB)

    return {"helper": helper, "binaries": binaries}


def _drain(node: dict[str, object], *, sync_exit: int, objects: int) -> str:
    binaries = node["binaries"]
    assert isinstance(binaries, Path)
    finished = subprocess.run(
        [str(node["helper"]), "drain"],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{binaries}{os.pathsep}{os.environ['PATH']}",
            "AWS_SYNC_EXIT": str(sync_exit),
            "AWS_LS_LINES": str(objects),
        },
    )
    assert finished.returncode == 0, finished.stderr
    lines = [line for line in finished.stdout.splitlines() if line.startswith("run\t")]
    assert len(lines) == 1, finished.stdout
    return lines[0]


def _verdict(line: str) -> str:
    return line.split("\t")[-1]


def test_a_skipped_file_is_not_a_failed_drain(node):
    """Exit 2 with every object present is the wandb dangling symlink, and it is fine.

    Two objects because ``flush_run`` writes ``edullm-drain.json`` into the run directory
    before it counts, so the disk holds that and ``log/train.log``.
    """
    assert _verdict(_drain(node, sync_exit=2, objects=2)) == "ok"


def test_a_transfer_that_actually_failed_is_still_a_failed_drain(node):
    """Exit 1 is `aws s3 sync`'s "at least one transfer failed", and must not be swallowed.

    Pinned with a *complete* listing so that the verdict can only be coming from the exit
    status: if this ever reads `ok`, the fix above went one step too far and the drain has
    stopped reporting the failure it exists for.
    """
    assert _verdict(_drain(node, sync_exit=1, objects=2)) == "failed"


def test_a_skipped_file_that_mattered_is_still_caught_by_the_count(node):
    """The count is the verdict, which is why letting exit 2 through loses nothing.

    A skipped file that was not a dangling symlink is a file the listing does not find, and
    ``short`` is the word for that.
    """
    assert _verdict(_drain(node, sync_exit=2, objects=1)) == "short"


def test_a_clean_sync_is_ok(node):
    """The unremarkable case, pinned so that the comparison cannot invert unnoticed."""
    assert _verdict(_drain(node, sync_exit=0, objects=2)) == "ok"
