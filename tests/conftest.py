"""Two arrangements the suite needs, and deliberately nothing else.

``tests/test_suite_budget.py`` measures what the whole session did, so it has to run
after the whole session has done it. Collection order is file name order, which would
put it somewhere in the middle and have it report a number that was still going up.

The second arrangement only matters under ``-n``. Each xdist worker is its own process
with its own memory of what it has verified, so where a test runs decides what the run
costs. Every test is given a group naming its module, which keeps a module's session
fixtures on one worker; the four modules that share one collection — and, on a nightly
run, one nested full-suite verification — are given the same group, so they share it in
parallel too rather than paying for it once each.

Nothing here changes what any test asserts or which tests run. ``uv run pytest -q`` runs
every test either way, and run serially the groups have no effect at all.
"""

from __future__ import annotations

import pytest

SESSION_BUDGET_MARKER = "session_budget"
GROUP_MARKER = "xdist_group"

#: The modules that must run in one process, and why. Every generator asks the same
#: question of the same tree, and the first to run pays for the answer while the rest read
#: it: a collection child on every run, and a full-suite child on a run that reproduces.
#: On separate workers there is no answer to read and each pays again — the multiplier
#: that proof_bundle's memory exists to remove, reappearing because the halves of it are
#: no longer in the same process. The budget joins them so that it is measuring a worker
#: that actually did the work it bounds.
SHARED_VERIFICATION_GROUP = "proof-verification"
SHARE_ONE_WORKER = frozenset(
    {
        "tests/test_phase0_proof.py",
        "tests/test_phase1_proof.py",
        "tests/test_phase2_proof.py",
        "tests/test_phase3_proof.py",
        "tests/test_suite_budget.py",
    }
)


def group_for(node_id: str) -> str:
    """Which worker's queue this test belongs in, named rather than numbered."""
    module = node_id.split("::", 1)[0]
    return SHARED_VERIFICATION_GROUP if module in SHARE_ONE_WORKER else module


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
