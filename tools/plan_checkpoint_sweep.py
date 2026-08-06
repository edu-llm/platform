"""Write the sweep plan a fan-out's cells read to find their own checkpoint.

Run from a laptop before submitting the array. Every cell of a Batch array runs the same
container overrides and differs only in its index, so the mapping from index to checkpoint has to
be somewhere both the submitter and the cells can reach. It goes beside the source run's
checkpoints, because the eval run has no id yet and the GPU workload role can already read
``teams/*/runs/*``.

``--dry-run`` prints the destination without writing. Run it first: the destination is derived
from the source run's own checkpoint prefix, and a wrong one is the single mistake here that is
not discovered until a GPU job is already reading the wrong object.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from edullm_platform.contracts.results import ResultManifest
from edullm_platform.weights import (
    plan_checkpoint_sweep,
    resolve_weights_from_run,
    sweep_plan_document,
)

LINEAGE_BUCKET = "sbsandbox-intern-edullm-lineage"

#: The segment separating a run's output prefix from the checkpoints under it. The plan is
#: written beside the checkpoints rather than among them, so this is where the URI is cut.
CHECKPOINT_SEGMENT = "/checkpoints/"


class S3ResultReader:
    """The one result manifest a run wrote, read through the CLI's own credentials.

    Through ``aws s3 cp`` rather than boto3 so that this tool needs no credential handling of
    its own and fails the same way every other tool in ``tools/`` does when a profile has
    expired.
    """

    def __init__(self, profile: str) -> None:
        self._profile = profile

    def result_manifests_for(self, run_id: str) -> list[ResultManifest]:
        body = subprocess.run(
            [
                "aws",
                "s3",
                "cp",
                f"s3://{LINEAGE_BUCKET}/result/{run_id}.json",
                "-",
                "--profile",
                self._profile,
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return [ResultManifest.model_validate(json.loads(body))]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--profile", default="sbsandbox")
    parser.add_argument("--limit", type=int, default=None, help="keep the first N cells")
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args()

    resolved = resolve_weights_from_run(S3ResultReader(arguments.profile), run_id=arguments.run_id)
    cells = plan_checkpoint_sweep(resolved)
    if arguments.limit is not None:
        cells = cells[: arguments.limit]
    document = sweep_plan_document(cells)

    print(f"{len(cells)} cells, {sum(1 for cell in cells if not cell.certified)} uncertified")
    for cell in cells:
        print(f"  {cell.index}  step {cell.step}  certified={cell.certified}")

    first = resolved.checkpoints[0].uri
    if CHECKPOINT_SEGMENT not in first:
        # Refused rather than guessed. Cutting on the last slash instead would put the plan
        # inside a checkpoint directory, where the cells do not look for it and where nothing
        # would report it missing until a GPU job had already started.
        print(
            f"{first!r} carries no {CHECKPOINT_SEGMENT!r} segment, so the prefix to write "
            "beside cannot be derived from it",
            file=sys.stderr,
        )
        return 2
    destination = f"{first.rsplit(CHECKPOINT_SEGMENT, maxsplit=1)[0]}/sweep.json"
    print(f"-> {destination}")
    if arguments.dry_run:
        return 0

    local = Path("/tmp/sweep.json")
    local.write_text(json.dumps(document, indent=2, sort_keys=True))
    subprocess.run(
        ["aws", "s3", "cp", str(local), destination, "--profile", arguments.profile],
        check=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
