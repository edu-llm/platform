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
from edullm_platform.criteria import CriteriaDefinitionError
from edullm_platform.criteria_runner import NestedExecutionError
from edullm_platform.phase1_gate import PHASE, evaluate_repository
from edullm_platform.status_prose import gate_and_pilot_line


def main() -> int:
    repo_root = Path.cwd()
    try:
        result = evaluate_repository(repo_root)
    except NestedExecutionError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except CriteriaDefinitionError as exc:
        print(f"the Phase 1 criteria definition is not usable: {exc}", file=sys.stderr)
        return 2
    except (OSError, json.JSONDecodeError, yaml.YAMLError, TypeError, ValidationError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI must map unexpected failures to exit 2
        print(str(exc), file=sys.stderr)
        return 2

    sys.stdout.write(canonical_json_bytes(result).decode("utf-8") + "\n")
    # Both verdicts, on stderr so that stdout stays exactly the canonical report a caller
    # parses. The exit code below is the gate's and only the gate's: a pilot-ready phase
    # with a red gate still exits 1, because the gate is what the exit code has always
    # meant and reusing it for adoption would silently change every caller's question.
    print(
        gate_and_pilot_line(phase=PHASE, gate_passed=result.passed, verdict=result.pilot),
        file=sys.stderr,
    )
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
