"""Which disk ``/scratch`` lands on, run rather than read.

**THE FAILURE THIS GUARDS IS SILENT AND COSTS THE WHOLE OF EVERY NODE'S LOCAL DISK.** The Deep
Learning Base AMI the launch resolves ships ``dlami-nvme.service`` enabled, and that unit
stripes every instance store device into one LVM volume and mounts it before user-data runs. So
by the time ``infra/block-node-bootstrap.sh`` reaches the disks they are in use, ``mkfs.ext4
-F`` refuses them, and the fallback the header describes -- ``/scratch`` on the root volume --
fires on every node in the fleet. It is not fatal and nothing surfaces it except one line in a
log nobody reads and a ``scratch_device`` field in the readiness sentinel, which is exactly the
kind of thing that is discovered on the Tuesday after a p5 window rather than during it.

**THESE TESTS RUN THE BLOCK RATHER THAN READING IT**, for the reason
``tests/test_block_node_claim.py`` gives about the helper: an assertion that the word ``lsblk``
appears would pass against every wrong thing this could do with the answer. The scratch section
is extracted, pointed at a stubbed ``lsblk``, ``mount``, ``mkfs.ext4`` and ``mdadm``, and asked
which disk it chose and what it ran to get there.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_PATH = PROJECT_ROOT / "infra" / "block-node-bootstrap.sh"

#: The scratch section, from the fallback it declares to the variable it hands to the sentinel.
#: Anchored on both ends so that moving either one fails here rather than silently testing a
#: shorter piece of the script than the comment above claims.
SCRATCH_SECTION = re.compile(
    r"^SCRATCH_DEVICE=root-volume\n(?P<body>.*?)^scratch_device=\"\$\{SCRATCH_DEVICE\}\"\n",
    re.MULTILINE | re.DOTALL,
)

#: What ``lsblk --nodeps --noheadings --output NAME,MODEL`` answers on the shape being
#: rehearsed. The model string is the only reliable discriminator between instance store and
#: EBS on Nitro, which is why the bootstrap matches on it and why these fixtures carry it.
ONE_EBS_ONE_INSTANCE_STORE = "nvme0n1 Amazon Elastic Block Store\nnvme1n1 Amazon EC2 NVMe Instance Storage\n"
ONE_EBS_FOUR_INSTANCE_STORE = "nvme0n1 Amazon Elastic Block Store\n" + "".join(
    f"nvme{index}n1 Amazon EC2 NVMe Instance Storage\n" for index in (1, 2, 3, 4)
)
NO_INSTANCE_STORE = "nvme0n1 Amazon Elastic Block Store\n"

LSBLK_STUB = """
if [ "${1:-}" = --nodeps ]; then
  cat "${LSBLK_DEVICES}"
  exit 0
fi
cat "${LSBLK_MOUNTPOINTS}"
exit 0
"""

RECORDING_STUB = 'echo "$(basename "$0") $*" >> "${DISK_LOG}"\nexit "${%s:-0}"\n'


def _write_stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/usr/bin/env bash\n{body}", encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def disks(tmp_path: Path) -> dict[str, Path]:
    """The scratch section of the bootstrap, runnable, with every disk command stubbed."""
    match = SCRATCH_SECTION.search(BOOTSTRAP_PATH.read_text(encoding="utf-8"))
    assert match is not None, "the bootstrap no longer has a scratch section shaped like this"

    scratch = tmp_path / "scratch"
    binaries = tmp_path / "bin"
    binaries.mkdir()

    script = tmp_path / "scratch-section.sh"
    script.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        f'SCRATCH="{scratch}"\n'
        "SCRATCH_DEVICE=root-volume\n"
        f"{match.group('body')}"
        'scratch_device="${SCRATCH_DEVICE}"\n'
        'echo "SCRATCH_DEVICE=${scratch_device}"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)

    _write_stub(binaries, "lsblk", LSBLK_STUB)
    for name in ("mount", "mkfs.ext4", "mdadm"):
        _write_stub(binaries, name, RECORDING_STUB % f"{name.split('.')[0].upper()}_STATUS")

    return {
        "script": script,
        "binaries": binaries,
        "scratch": scratch,
        "log": tmp_path / "disk.log",
        "devices": tmp_path / "devices.txt",
        "mountpoints": tmp_path / "mountpoints.txt",
    }


def _run(
    disks: dict[str, Path], *, devices: str, mountpoints: str, mkfs_status: int = 0
) -> tuple[str, list[str]]:
    disks["devices"].write_text(devices, encoding="utf-8")
    disks["mountpoints"].write_text(mountpoints, encoding="utf-8")
    disks["log"].write_text("", encoding="utf-8")
    completed = subprocess.run(
        ["bash", str(disks["script"])],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{disks['binaries']}{os.pathsep}{os.environ['PATH']}",
            "LSBLK_DEVICES": str(disks["devices"]),
            "LSBLK_MOUNTPOINTS": str(disks["mountpoints"]),
            "DISK_LOG": str(disks["log"]),
            "MKFS_STATUS": str(mkfs_status),
        },
    )
    assert completed.returncode == 0, completed.stderr
    chosen = next(
        line.partition("=")[2]
        for line in completed.stdout.splitlines()
        if line.startswith("SCRATCH_DEVICE=")
    )
    return chosen, disks["log"].read_text(encoding="utf-8").splitlines()


def test_a_disk_the_image_already_mounted_is_used_rather_than_reformatted(
    disks: dict[str, Path],
) -> None:
    """THE ONE THAT HAPPENS ON EVERY NODE OF A REAL FLEET.

    Mutation: go straight to ``mkfs`` the way this did before. ``dlami-nvme.service`` has the
    devices, ``mkfs.ext4 -F`` answers "apparently in use by the system", and ``/scratch`` is on
    the 500 GiB root volume rather than on the array -- on all eight machines, reported nowhere
    a person looks.
    """
    chosen, ran = _run(
        disks,
        devices=ONE_EBS_ONE_INSTANCE_STORE,
        mountpoints="\n/opt/dlami/nvme\n",
    )

    assert chosen == "/opt/dlami/nvme"
    assert [line for line in ran if line.startswith("mount ")] == [
        f"mount --bind /opt/dlami/nvme {disks['scratch']}"
    ]
    assert not [line for line in ran if line.startswith(("mkfs", "mdadm"))], (
        "the image had already built this array and the bootstrap rebuilt it anyway"
    )


def test_disks_nothing_has_claimed_are_still_striped_and_formatted(
    disks: dict[str, Path],
) -> None:
    """The path the section was written for, which the fix above must not have taken away.

    An image that leaves the instance store alone -- which is every AMI family this lane is not
    currently pinned to -- still gets the RAID0 across all of the devices rather than one mount
    per disk.
    """
    chosen, ran = _run(disks, devices=ONE_EBS_FOUR_INSTANCE_STORE, mountpoints="\n\n\n\n")

    assert chosen == "/dev/md0"
    assert any(
        line.startswith("mdadm --create") and "--raid-devices=4" in line for line in ran
    ), ran
    assert "mkfs.ext4 -F -m 0 /dev/md0" in ran
    assert f"mount -o discard,noatime /dev/md0 {disks['scratch']}" in ran


def test_a_shape_with_no_instance_store_falls_back_to_the_root_volume(
    disks: dict[str, Path],
) -> None:
    """Recorded rather than fatal, which is the line the bootstrap header draws: the node still
    trains, it just trains against the root volume, and a person can read that off the
    sentinel and route around it."""
    chosen, ran = _run(disks, devices=NO_INSTANCE_STORE, mountpoints="")

    assert chosen == "root-volume"
    assert not ran, ran


def test_a_format_that_fails_still_falls_back_rather_than_aborting(
    disks: dict[str, Path],
) -> None:
    """``prepare_scratch`` is called as an ``if`` condition so that ``set -e`` is suspended
    inside it. Losing a node out of eight because one disk command failed is the trade the
    bootstrap header refuses to make."""
    chosen, _ = _run(
        disks, devices=ONE_EBS_ONE_INSTANCE_STORE, mountpoints="\n", mkfs_status=1
    )

    assert chosen == "root-volume"
