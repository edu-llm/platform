"""Write the recorded canonical digests under `fixtures/goldens/`.

RUNNING THIS IS A DECISION, NOT A REPAIR. The digests are a tripwire, and the failure they
produce says which artifact moved and by how much. Re-recording a moved digest is right only
when the change that moved it was intended, and then the diff belongs in the same commit as
that change so a person approves the new digest rather than absorbing it.

By default it refuses to overwrite a digest that has drifted, and prints what moved.
`--force` re-records anyway, which is the deliberate half.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from edullm_platform.contract_inventory import (
    INVENTORY_DRIFT_GUIDANCE,
    inventory_drift,
    load_recorded_models,
    model_records,
    render_inventory_document,
)
from edullm_platform.contract_inventory import recorded_path as inventory_path
from edullm_platform.serialization_goldens import (
    GOLDEN_SETS,
    GoldensError,
    golden_drift,
    golden_drift_guidance,
    load_recorded_goldens,
    recorded_path,
    render_goldens_document,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-record a digest that has drifted, having read what moved",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    repo_root = arguments.repo_root
    guidance = golden_drift_guidance()
    written: list[Path] = []
    for golden_set in GOLDEN_SETS:
        try:
            live = golden_set.live(repo_root)
        except (GoldensError, OSError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        path = recorded_path(repo_root, golden_set)
        if path.exists() and not arguments.force:
            drift = golden_drift(load_recorded_goldens(path), live)
            if drift:
                for entry in drift:
                    print(
                        guidance.format(
                            fixture=entry.fixture,
                            contract=entry.contract,
                            recorded=entry.recorded,
                            live=entry.live,
                        ),
                        file=sys.stderr,
                    )
                    print(file=sys.stderr)
                print(
                    f"{path} was not rewritten. Pass --force once you have read the above.",
                    file=sys.stderr,
                )
                return 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            render_goldens_document(live, subject=golden_set.subject), encoding="utf-8"
        )
        written.append(path)

    live_models = model_records()
    inventory = inventory_path(repo_root)
    if inventory.exists() and not arguments.force:
        moved = inventory_drift(load_recorded_models(inventory), live_models)
        if moved:
            for record in moved:
                print(
                    INVENTORY_DRIFT_GUIDANCE.format(
                        subject=record.subject,
                        field=record.field,
                        recorded=record.recorded,
                        live=record.live,
                    ),
                    file=sys.stderr,
                )
                print(file=sys.stderr)
            print(
                f"{inventory} was not rewritten. Pass --force once you have read the above.",
                file=sys.stderr,
            )
            return 1
    inventory.parent.mkdir(parents=True, exist_ok=True)
    inventory.write_text(render_inventory_document(live_models), encoding="utf-8")
    written.append(inventory)

    for path in written:
        print(path.relative_to(repo_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
