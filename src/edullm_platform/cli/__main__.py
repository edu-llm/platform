"""``python -m edullm_platform.cli``, for a checkout with no console script installed."""

from __future__ import annotations

from edullm_platform.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
