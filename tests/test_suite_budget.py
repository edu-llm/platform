"""What one session is allowed to spend on verifying its own tree.

The expensive thing this suite does is run itself. Every proof generator verifies the
tree it describes with a child pytest over every test in the repository, and there is
one generator per phase. Three phases meant three full runs of the same unchanged tree
and 78% of the wall clock, which nobody noticed happening because each generator was
individually reasonable.

That is the failure this module exists to catch, and it is a budget rather than a unit
test because the defect is not in any one place. A fourth phase that copies the third,
a cache key that grows a field nobody meant it to depend on, an environment variable
that stops matching: each of those is a sensible-looking change that quietly puts the
multiplier back, and the only symptom is that CI takes seven minutes again.

These run last. ``tests/conftest.py`` moves anything marked ``session_budget`` to the end
of the session, because a budget read in the middle reports a number that is still
going up.

Under ``-n`` each worker is its own process with its own count, so the budget is per
worker and reads at most one on each of them. That is the honest reading: the cost being
bounded is a per-process cost. The reuse itself is proved directly, and without any
dependence on session ordering, in ``tests/test_verification_reuse.py``.
"""

from __future__ import annotations

import pytest

from edullm_platform.proof_bundle import collection_child_runs, full_suite_child_runs

pytestmark = pytest.mark.session_budget


def test_a_session_runs_the_full_suite_at_most_once() -> None:
    started = full_suite_child_runs()

    assert started <= 1, (
        f"this session started {started} full-suite pytest children. Each one runs every "
        "test in the repository, and running the same unchanged tree twice measures "
        "nothing the first run did not. Something has stopped run_full_suite recognising "
        "two requests as the same question — most likely a new generator whose nested "
        "environment or ignore list differs from the others."
    )


def test_a_session_collects_the_tree_at_most_once() -> None:
    started = collection_child_runs()

    assert started <= 1, (
        f"this session started {started} collection pytest children against the tree. "
        "Collection is the same question for every generator and its answer does not "
        "change between them."
    )
