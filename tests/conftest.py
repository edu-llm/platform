"""Two arrangements the suite needs, and one thing it refuses to run without noticing.

``tests/test_suite_grouping.py`` reads what the whole session was arranged into, so it has
to run after the whole session has been arranged. Collection order is file name order, which
would put it somewhere in the middle.

The second arrangement only matters under ``-n``. Each xdist worker is its own process, so a
module whose session fixtures are expensive pays for them once per worker it lands on. Every
test is given a group naming its module, which keeps a module on one worker.

Neither of those changes what any test asserts or which tests run. ``uv run pytest -q``
runs every test either way, and run serially the groups have no effect at all.

The third is a guard rather than an arrangement, and it is here because the grouping above
is what hid the bug it exists for. ``--dist loadgroup`` keeps a module's tests together and
says nothing about which worker gets which module, so two files that damage each other only
do so when they land on the same one. That is a coin flip per run: the same commit is green
on a pull request and red on ``main``. ``tests/module_identity.py`` fails the moment the
damage is *set up* -- a name in ``sys.modules`` rebound to a second copy of a file -- which
happens in every ordering, rather than when it lands, which happens in some of them.
"""

from __future__ import annotations

import pytest

# Re-exported rather than restated, because pytest reads hooks out of a conftest namespace
# and `tests/test_module_identity.py` has to be able to prove that this exact one fails a
# run. Both halves of that only work if there is one copy of it, which is the subject.
from module_identity import pytest_runtest_call  # noqa: F401

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


