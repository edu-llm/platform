"""A run fails if it ever rebinds a name in ``sys.modules`` to a second copy of a file.

Not collected by pytest: the filename deliberately does not start with ``test_``.
``tests/conftest.py`` re-exports the hook below, because a conftest is where pytest looks
for one; it lives here so that `tests/test_module_identity.py` can prove the guard fails by
running *this* hook rather than a restatement of it.

WHAT THIS CATCHES, AND WHY IT IS NOT THE OBVIOUS CHECK
------------------------------------------------------
``tools/`` is not a package, so several test modules build a module object out of a file
path and register it. Four times now one of them has registered a *second* object under a
name that already held one:

* ``parser_for`` in `tests/test_workflow_tool_arguments.py` registered every tool a workflow
  runs under the tool's own name, replacing the copy `tests/test_find_runs_that_saved_nothing.py`
  had already bound its entry point from. The monkeypatch that followed landed on an object
  nothing called, the real object store ran, and CI reached AWS with no credentials.
* ``load_tool`` in `tests/test_audit_workflow.py` did the same to
  ``tools/visibility_board.py``, and five tests in `tests/test_visibility_board.py` shelled
  out to the real ``aws``.
* ``load`` in `tests/test_deployed_stacks.py` did it to ``tools/verify_deployed_stacks.py``
  and harmed nothing, because both files happen to keep the object they were handed instead
  of looking the name up again. That is a property of how those two are written today.
* ``build_run_history`` in `tests/test_build_run_history.py` rebuilt
  ``tools/build_run_history.py`` on every one of the seven tests that call it, so the name
  meant a different object in each. Landed after this guard was written and was the first
  thing the guard caught on being rebased onto it, which is the argument for having it: it
  was written by somebody who had read neither the incidents above nor this file.

Every one of them was invisible in isolation and every one turned on which xdist worker got
which file, so they passed pull-request CI and went red on ``main``.

THE OBVIOUS GUARD DOES NOT WORK, WHICH IS THE REASON THIS ONE IS SHAPED LIKE IT IS.
"Fail when two live objects for the same file exist" sounds like the check and is not one.
Rebinding a name leaves exactly one entry in ``sys.modules`` -- the second copy -- while the
damage is done by the first, which survives only as the ``__globals__`` of functions another
module imported before the swap. Both were written and run against the `visibility_board`
failure with its fix reverted: counting ``sys.modules`` keys per file reported nothing, and
a ``gc`` sweep for live module objects reported nothing either, because the superseded copy
is collected. Either would have shipped green over a live bug.

What all three incidents have in common is the rebinding, so that is what is watched. The
check deliberately does not ask whether anybody was holding the old copy: that is a fact
about the rest of the suite on the day it is asked rather than about the loader doing the
rebinding, and it is exactly how a harmless one becomes a red ``main`` two months later.

WHAT IT DOES NOT CATCH
----------------------
Two copies of one file under *different* names -- ``tools.report_run_costs`` from a test
against ``report_run_costs`` from a tool importing its neighbour by bare name. There were 28
of those when this was written, none of them able to fail: every pair of files that shares
one was run together in one process, in both orders, and none produced a failure that the
files did not produce alone. They are the setup for the same accident and not the accident,
and failing on them would mean either a 28-entry inventory or a restatement of how every
tool imports its neighbours, so this reports nothing about them.

A name imported and replaced inside a single test body, which is sampled holding only the
second object and reads as ordinary. `tests/test_module_identity.py` keeps that limit as a
passing test rather than a note. It is close to self-limiting: being harmed needs somebody
else to hold the discarded copy, and that somebody imports at module scope, which happens
during collection and therefore before any test body runs.

A rebinding after the last test on a worker has finished. There is nothing left to harm,
and a check there could not report anyway -- one was written, and it failed a serial run
correctly and exited 0 under ``-n2``, because a worker's exit status is not the run's. A
guard path that cannot fail under the flags CI uses is worse than no path, so there isn't
one.

Deleting a name and importing it again reads as a rebinding, and is meant to: the stale
references it leaves behind are the same ones however the second copy was arrived at.

COST
----
A full pass over ``sys.modules`` runs only when its size changes rather than once per test;
every other check is one lookup per already-seen repository-local name. Measured across the
suite at 0.17s on the busiest of four xdist workers.
"""

from __future__ import annotations

import sys
from collections.abc import Generator, Mapping
from pathlib import Path
from types import ModuleType

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_ROOT = str(PROJECT_ROOT)

#: Everything installed rather than written here. A rebinding inside a dependency is not
#: this suite's to report, and ``.venv`` sits under the project root, so a prefix test on
#: its own would sweep the whole of site-packages into scope.
FOREIGN = ("site-packages", "dist-packages", f"{Path('/.venv/')}")


class RebindingSeen(AssertionError):
    """Raised for the test in whose window a name changed which module object it names."""


def local_file(module: object) -> str | None:
    """The file a module was loaded from, if it is one this repository owns."""
    if not isinstance(module, ModuleType):
        return None
    filename = getattr(module, "__file__", None)
    if not isinstance(filename, str) or not filename.startswith(_ROOT):
        return None
    if any(marker in filename for marker in FOREIGN):
        return None
    return filename


def explain(name: str, filename: str) -> str:
    return (
        f"`{name}` was rebound to a second module object for {Path(filename).name}, so this "
        "session now holds two copies of that file. Whatever imported a function from the "
        "first copy still calls into it, while `monkeypatch.setattr` on this name reaches "
        "the second -- a stub that does not take, and a test that runs the real thing. It "
        "is invisible when either file is run alone and it turns on which xdist worker "
        "collected what, so it passes pull-request CI and fails on main. Return the module "
        "already in sys.modules rather than building another: `load_tool` in "
        "tests/test_audit_workflow.py is the shape to copy, and tests/module_identity.py "
        "is why."
    )


class ModuleIdentity:
    """Remembers which object each repository-local name meant, and notices a change.

    Takes the mapping to look at, defaulting to the real ``sys.modules``, so that the test
    proving this can fail drives this class over a dictionary it controls rather than a
    reimplementation of it.
    """

    def __init__(self) -> None:
        self._seen: dict[str, tuple[int, str]] = {}
        self._size = -1

    def rebindings(self, modules: Mapping[str, object] | None = None) -> tuple[str, ...]:
        """Names that now mean a different object for the same file than they last did.

        Each is reported once: the new object becomes what the name means afterwards, so a
        loader called in a loop fails the test that called it rather than every test that
        runs after it.
        """
        live: Mapping[str, object] = sys.modules if modules is None else modules
        if len(live) == self._size:
            return self._among_seen(live)
        self._size = len(live)
        return self._among_all(live)

    def _among_seen(self, live: Mapping[str, object]) -> tuple[str, ...]:
        """The cheap pass. A name not seen yet cannot have been rebound yet."""
        found: list[str] = []
        for name, (identity, filename) in list(self._seen.items()):
            module = live.get(name)
            if module is None or id(module) == identity or local_file(module) != filename:
                continue
            self._seen[name] = (id(module), filename)
            found.append(explain(name, filename))
        return tuple(found)

    def _among_all(self, live: Mapping[str, object]) -> tuple[str, ...]:
        """The full pass, which also takes in names imported since the last one."""
        found: list[str] = []
        for name, module in list(live.items()):
            filename = local_file(module)
            if filename is None:
                continue
            previous = self._seen.get(name)
            self._seen[name] = (id(module), filename)
            if previous is None or previous[0] == id(module) or previous[1] != filename:
                continue
            found.append(explain(name, filename))
        return tuple(found)


_identity = ModuleIdentity()


def refuse_a_second_copy() -> None:
    found = _identity.rebindings()
    if found:
        raise RebindingSeen("\n\n".join(found))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, object, object]:
    """Checked either side of every test body, so the report names the file that did it.

    Before, for a rebinding done during collection or by a fixture; after, for one done by
    the test just run. Between them the failing node id sits next to the loader responsible,
    which is most of the work of fixing it -- all three real ones presented as unrelated
    tests in a different file failing at random.

    In the call phase rather than in setup or teardown because raising in either of those
    leaves pytest's own setup stack half-unwound, and what a reader gets then is ``previous
    item was not torn down properly`` out of ``runner.py`` instead of the message above. A
    wrapper around the call fails the test the ordinary way and lets its fixtures finalise.
    """
    refuse_a_second_copy()
    result = yield
    refuse_a_second_copy()
    return result
