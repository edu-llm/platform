"""One arrangement the suite needs, and deliberately nothing else.

``tests/test_suite_budget.py`` measures what the whole session did, so it has to run
after the whole session has done it. Collection order is file name order, which would
put it somewhere in the middle and have it report a number that was still going up.

Nothing here changes what any test asserts or which tests run. ``uv run pytest -q`` runs
every test either way; this only decides when one of them is asked its question.
"""

from __future__ import annotations

import pytest

SESSION_BUDGET_MARKER = "session_budget"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Move anything marked ``session_budget`` to the end, in its existing order."""
    items.sort(key=lambda item: item.get_closest_marker(SESSION_BUDGET_MARKER) is not None)
