"""Two arrangements the suite needs, and deliberately nothing else.

``tests/test_suite_grouping.py`` reads what the whole session was arranged into, so it has
to run after the whole session has been arranged. Collection order is file name order, which
would put it somewhere in the middle.

The second arrangement only matters under ``-n``. Each xdist worker is its own process, so a
module whose session fixtures are expensive pays for them once per worker it lands on. Every
test is given a group naming its module, which keeps a module on one worker.

Nothing here changes what any test asserts or which tests run. ``uv run pytest -q`` runs
every test either way, and run serially the groups have no effect at all.
"""

from __future__ import annotations

import pytest

SESSION_BUDGET_MARKER = "session_budget"
GROUP_MARKER = "xdist_group"


def group_for(node_id: str) -> str:
    """Which worker's queue this test belongs in, named rather than numbered."""
    return node_id.split("::", 1)[0]


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Order the session budget last, and decide which worker may run what.

    ``tryfirst`` is load-bearing under ``--dist loadgroup`` and not a style choice. xdist
    reads the group off each item and encodes it into the node id from a hook of its own,
    and only sees the marks that already exist when it runs. Applied after it, these marks
    are ignored, and loadgroup falls back to distributing individual tests — which
    rebuilds every session fixture on every worker and measured 88s against 47s. Nothing
    fails when that happens, which is exactly why it is written down here.
    """
    items.sort(key=lambda item: item.get_closest_marker(SESSION_BUDGET_MARKER) is not None)
    for item in items:
        item.add_marker(pytest.mark.xdist_group(group_for(item.nodeid)))
