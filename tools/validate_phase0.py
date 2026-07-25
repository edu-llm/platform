from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from edullm_platform.phase0_gate import evaluate_repository


def main() -> int:
    repo_root = Path.cwd()
    try:
        result = evaluate_repository(repo_root)
    except (OSError, TypeError, ValidationError, yaml.YAMLError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(
        json.dumps(
            result.model_dump(mode="json", by_alias=True),
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
