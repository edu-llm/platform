"""How the suite is distributed across workers, and what stops that quietly regressing.

Under ``--dist loadgroup`` a test with no group is distributed on its own, which rebuilds
every session fixture on every worker. Measured at 88s against 47s. Nothing fails when it
happens, which is exactly why it is asserted here.

The three cases below are the three ways the arrangement can be lost without anybody
noticing: a test that reaches collection with no group, a hook that applies the groups after
xdist has already read them, and a configuration file that makes the standard command run
less than it says.

These run last. ``tests/conftest.py`` moves anything marked ``session_budget`` to the end of
the session, because a session-wide reading taken in the middle is still going up.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import conftest
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: Files pytest reads configuration from. Only pyproject.toml is meant to exist; a second
#: one is the obvious place to hide a default that makes the standard command run less.
OTHER_CONFIG_FILES = ("pytest.ini", "setup.cfg", "tox.ini")

pytestmark = pytest.mark.session_budget


def test_every_test_is_assigned_exactly_one_worker_group(request: pytest.FixtureRequest) -> None:
    """Under ``--dist loadgroup``, a test with no group is distributed on its own.

    That is the expensive default this suite must never fall back into: it rebuilds every
    session fixture on every worker, and it measured 88s against 47s. It fails nothing while
    it happens, so it is asserted here.
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


def test_no_configured_default_makes_the_standard_command_a_subset() -> None:
    """``uv run pytest -q`` runs every test, and no config file may change that.

    ``slow`` is an opt-out a developer types when they want a quick loop. Written into
    ``addopts`` instead it becomes the default, and then the command every contributing note
    asks for quietly runs less than it says. The first anybody would learn of it is a green
    pull request that broke something the suite covers, which is the failure a suite exists
    to prevent.
    """
    configuration = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    settings = configuration["tool"]["pytest"]["ini_options"]

    assert "addopts" not in settings, (
        "pytest addopts is configured. Whatever it adds is now part of every run of the "
        "standard command, including the deselection this marker exists to keep optional."
    )
    assert [name for name in OTHER_CONFIG_FILES if (PROJECT_ROOT / name).exists()] == []
