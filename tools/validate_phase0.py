from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from edullm_platform.canonical import canonical_json_bytes
from edullm_platform.phase0_gate import evaluate_repository


def main() -> int:
    repo_root = Path.cwd()
    try:
        result = evaluate_repository(repo_root)
    except (OSError, json.JSONDecodeError, yaml.YAMLError, TypeError, ValidationError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    sys.stdout.write(canonical_json_bytes(result).decode("utf-8") + "\n")
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
