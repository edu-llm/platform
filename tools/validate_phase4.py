from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from edullm_platform.phase4_gate import PHASE, evaluate_repository
from edullm_platform.phase_gate import run_gate_command


def main() -> int:
    return run_gate_command(phase=PHASE, evaluate=evaluate_repository)


if __name__ == "__main__":
    sys.exit(main())
