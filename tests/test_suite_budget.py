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

Under ``-n`` each worker is its own process with its own count, so what is bounded is a
per-process cost. ``tests/conftest.py`` puts this module in the same worker group as both
generators, so on a full parallel run the process reading the budget is the process that
did the verifying and the number means something.

It cannot mean something everywhere. A filtered run that selects no generator — ``-m 'not
slow'``, or a ``-k`` on one module — leaves a process with nothing to verify, and the
budget reads zero and passes. That is the correct answer for that process and there is no
honest way to tell it apart from a regression without the test reimplementing the
scheduler's knowledge of who ran what. The reuse itself is proved directly, and without
any dependence on ordering or on which worker anything landed on, in
``tests/test_verification_reuse.py``.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import conftest
import pytest

from edullm_platform.proof_bundle import (
    GENERATOR_TEST_PATHS,
    collection_child_runs,
    full_suite_child_runs,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: Files pytest reads configuration from. Only pyproject.toml is meant to exist; a second
#: one is the obvious place to hide a default that makes the standard command run less.
OTHER_CONFIG_FILES = ("pytest.ini", "setup.cfg", "tox.ini")

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


def test_every_test_is_assigned_exactly_one_worker_group(request: pytest.FixtureRequest) -> None:
    """Under ``--dist loadgroup``, a test with no group is distributed on its own.

    That is the expensive default this suite must never fall back into: it rebuilds every
    session fixture on every worker, including the nested full-suite verification, and it
    measured 88s against 47s. It fails nothing while it happens, so it is asserted here.
    """
    ungrouped = [
        item.nodeid
        for item in request.session.items
        if not list(item.iter_markers(conftest.GROUP_MARKER))
    ]

    assert ungrouped == []


def test_the_grouping_is_applied_before_xdist_reads_it() -> None:
    """The hook that assigns groups must run before the one that acts on them.

    xdist encodes each test's group into its node id from a hook of its own and sees only
    the marks that already exist when it runs. Losing ``tryfirst`` means the marks are
    applied too late, every one of them is ignored, and the suite silently takes twice as
    long. Nothing else notices, so this does.
    """
    options = conftest.pytest_collection_modifyitems.pytest_impl

    assert options["tryfirst"] is True
    assert options["trylast"] is False


def test_the_two_generators_and_this_budget_share_one_worker() -> None:
    # The two of them share one verification, which only exists to be shared inside a
    # single process. The budget joins them so that it is reading a worker that verified
    # something rather than one that happened to get none of the work it measures.
    groups = {
        conftest.group_for(f"{module}::test_x")
        for module in (*GENERATOR_TEST_PATHS, "tests/test_suite_budget.py")
    }

    assert groups == {conftest.SHARED_VERIFICATION_GROUP}


def test_no_configured_default_makes_the_standard_command_a_subset() -> None:
    """``uv run pytest -q`` runs every test, and no config file may change that.

    ``slow`` is an opt-out a developer types when they want a quick loop. Written into
    ``addopts`` instead it becomes the default, and then the command every proof bundle
    and every contributing note asks for quietly runs less than it says. The first
    anybody would learn of it is a green pull request that broke something the suite
    covers, which is the failure a suite exists to prevent.
    """
    configuration = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    settings = configuration["tool"]["pytest"]["ini_options"]

    assert "addopts" not in settings, (
        "pytest addopts is configured. Whatever it adds is now part of every run of the "
        "standard command, including the deselection this marker exists to keep optional."
    )
    assert [name for name in OTHER_CONFIG_FILES if (PROJECT_ROOT / name).exists()] == []
