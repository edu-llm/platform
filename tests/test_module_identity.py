"""That the guard in ``tests/module_identity.py`` fails a run holding two copies of a file.

A guard nobody has watched fail is a guard nobody knows the state of, and this repository
has shipped four of those. So the condition here is not described, it is *rebuilt*: these
tests construct a second module object for a real tool the same way the loaders that caused
the three real incidents did, and check that the guard reports it.

Two halves, because they answer different questions. The first drives ``ModuleIdentity``
over a mapping of real module objects and asks whether the detector sees the rebinding. The
second runs a whole child pytest session whose conftest re-exports the same three hooks this
suite's conftest does, over a test that reintroduces the condition, and asks whether a run
that does it goes red. A detector that reports into nothing would pass the first and fail
the second, which is why the second exists.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
from module_identity import ModuleIdentity, RebindingSeen, local_file

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: The tool the second of the three incidents was about. Used rather than a stand-in so that
#: what is rebuilt below is the file that actually did this, and so that a rename of it
#: fails here rather than quietly leaving this testing nothing.
SUBJECT = PROJECT_ROOT / "tools" / "visibility_board.py"


def a_second_copy_of(path: Path, name: str) -> ModuleType:
    """A module object built from a file, exactly as the loaders that caused this do.

    Not executed. Running ``tools/visibility_board.py``'s body a second time costs a second
    of imports and proves nothing extra: the guard compares identity, and an unexecuted
    module object is as distinct from the first as an executed one is.
    """
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification is not None
    return importlib.util.module_from_spec(specification)


def test_the_subject_of_this_file_is_a_tool_that_is_still_there() -> None:
    """Mutation: rename the tool and leave this file alone.

    Everything below builds module objects out of `SUBJECT`, and `spec_from_file_location`
    is perfectly happy to do that for a path that does not exist. Without this, a rename
    leaves a file full of tests that pass while watching nothing.
    """
    assert SUBJECT.is_file(), f"{SUBJECT} is gone, so every test in this file is vacuous"


def test_a_name_rebound_to_a_second_copy_of_one_file_is_reported() -> None:
    """The condition the three incidents had in common, rebuilt and handed to the detector."""
    watcher = ModuleIdentity()
    first = a_second_copy_of(SUBJECT, "visibility_board")
    modules: dict[str, object] = {"visibility_board": first}

    assert watcher.rebindings(modules) == ()

    modules["visibility_board"] = a_second_copy_of(SUBJECT, "visibility_board")
    found = watcher.rebindings(modules)

    assert len(found) == 1
    assert "visibility_board" in found[0]


def test_a_name_that_keeps_meaning_the_same_object_is_not_reported() -> None:
    """The other direction, which is most of the suite. Mutation: report on every sighting.

    A guard that fired on a name it had merely seen twice would fail every run, be turned
    off in a week, and take the real check with it.
    """
    watcher = ModuleIdentity()
    modules: dict[str, object] = {"visibility_board": a_second_copy_of(SUBJECT, "x")}

    assert watcher.rebindings(modules) == ()
    assert watcher.rebindings(modules) == ()
    assert watcher.rebindings(dict(modules)) == ()


def test_the_same_rebinding_is_reported_once_rather_than_for_every_test_afterwards() -> None:
    """Mutation: leave the remembered identity alone after reporting it.

    One loader called in a loop would then fail every test that ran after it, and the node
    id in the report -- which is the whole of how a reader finds the loader -- would name a
    file that had nothing to do with it.
    """
    watcher = ModuleIdentity()
    modules: dict[str, object] = {"visibility_board": a_second_copy_of(SUBJECT, "v")}
    watcher.rebindings(modules)
    modules["visibility_board"] = a_second_copy_of(SUBJECT, "v")

    assert len(watcher.rebindings(modules)) == 1
    assert watcher.rebindings(modules) == ()


def test_a_name_that_starts_meaning_a_different_file_is_not_a_rebinding() -> None:
    """Two files taking turns under one name is a different thing and not this one.

    It is what a test that swaps a stub in for a module does deliberately, and reporting it
    would make the guard fire on ordinary work.
    """
    watcher = ModuleIdentity()
    other = PROJECT_ROOT / "tools" / "report_run_costs.py"
    modules: dict[str, object] = {"shared": a_second_copy_of(SUBJECT, "shared")}
    watcher.rebindings(modules)

    modules["shared"] = a_second_copy_of(other, "shared")

    assert watcher.rebindings(modules) == ()


def test_a_rebinding_inside_an_installed_dependency_is_not_this_suites_to_report() -> None:
    """Mutation: drop the site-packages test from `local_file` and this fails.

    ``.venv`` is under the project root, so a check that only asked whether a file was
    beneath it would put every installed package in scope -- and pytest's own plugin loading
    moves modules around in ways this has no business failing a run over.
    """
    installed = next(
        (module for name, module in sys.modules.items() if "site-packages" in str(
            getattr(module, "__file__", "")
        )),
        None,
    )
    if installed is None:  # pragma: no cover - a venv with no third-party import
        pytest.skip("nothing imported from site-packages in this session")

    assert local_file(installed) is None


def test_something_that_is_not_a_module_at_all_is_ignored() -> None:
    """``sys.modules`` holds ``None`` for a failed import, and lazy loaders put objects in it."""
    assert local_file(None) is None
    assert local_file("not a module") is None


# ------------------------------------------------------------------------------------------
# The wiring, proved by a child session rather than by reading the conftest
# ------------------------------------------------------------------------------------------

CHILD_CONFTEST = """
from module_identity import (  # noqa: F401
    pytest_runtest_call,
    pytest_sessionfinish,
    pytest_terminal_summary,
)
"""

#: The victim's half, and the reason it is at module scope. `tests/test_visibility_board.py`
#: imports the tool while it is being collected, which is what leaves a copy for a loader to
#: replace and what makes its own functions close over the copy being replaced.
VICTIM = """
import sys
from pathlib import Path

sys.path.insert(0, {tools!r})

from visibility_board import read_tagged_resources  # noqa: E402,F401


def test_the_victim_does_something_ordinary() -> None:
    assert callable(read_tagged_resources)
"""

#: `load_tool` in `tests/test_nightly_workflow.py` as it was before it was fixed, against
#: the tool it did it to. Two files rather than one, because that is the arrangement: the
#: copy being discarded belongs to somebody else, and the loader has no idea.
CULPRIT = """
import importlib.util
import sys
from pathlib import Path

TOOLS = Path({tools!r})


def load_tool(name):
    tool = TOOLS / (name + ".py")
    specification = importlib.util.spec_from_file_location(name, tool)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def test_the_culprit_reads_a_constant_off_the_tool() -> None:
    assert load_tool("visibility_board") is not None
"""


def child_run(tmp_path: Path, files: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """A whole pytest session over ``files``, with this suite's own guard hooks installed.

    A child rather than this session, because a guard cannot be watched failing from inside
    the run it would fail. The file names carry the order: pytest collects in name order, so
    ``a_`` before ``b_`` puts the victim's module-level import ahead of the loader that
    replaces what it imported.
    """
    (tmp_path / "conftest.py").write_text(CHILD_CONFTEST, encoding="utf-8")
    for name, body in files.items():
        (tmp_path / name).write_text(
            body.format(tools=str(PROJECT_ROOT / "tools")), encoding="utf-8"
        )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(PROJECT_ROOT / "tests"), str(PROJECT_ROOT / "src"), str(PROJECT_ROOT)]
    )
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(tmp_path)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


@pytest.mark.slow
def test_a_child_run_that_reintroduces_the_condition_goes_red(tmp_path: Path) -> None:
    """The guard, end to end, against the loader and the victim that produced the incident.

    Mutation: have the hooks report without raising, or check only once per test instead of
    either side of it, and this passes. It is the half a detector wired into nothing would
    survive, and this repository has shipped four guards that were exactly that.

    What it does *not* prove is that this suite runs these hooks, because the child brings
    its own conftest; `test_this_suites_conftest_installs_the_hooks_this_file_proves` is
    that half, and neither is worth anything without the other.
    """
    child = child_run(tmp_path, {"test_a_victim.py": VICTIM, "test_b_culprit.py": CULPRIT})

    assert child.returncode != 0, (
        "a run that rebound `visibility_board` to a second module object passed:\n"
        f"{child.stdout}\n{child.stderr}"
    )
    assert "RebindingSeen" in child.stdout, (
        "the child failed, but not on the guard, so it proves nothing about it:\n"
        f"{child.stdout}\n{child.stderr}"
    )
    assert "visibility_board" in child.stdout


@pytest.mark.slow
def test_a_child_run_that_reads_the_tool_once_stays_green(tmp_path: Path) -> None:
    """The control. Mutation: fail on any import of a tool at all, and this catches it.

    Without it a guard that failed every child run would pass the test above and read as
    working, which is the failure this whole file exists to make impossible.
    """
    child = child_run(tmp_path, {"test_a_victim.py": VICTIM})

    assert child.returncode == 0, f"{child.stdout}\n{child.stderr}"


@pytest.mark.slow
def test_a_rebinding_wholly_inside_one_test_body_is_a_known_blind_spot(tmp_path: Path) -> None:
    """Written down as a passing run rather than left for somebody to discover.

    The check runs either side of a test body, so a name that is imported *and* replaced
    inside one of them is never sampled holding the first object and reads as ordinary. It
    is on the record here because a limit nobody has written down is indistinguishable from
    one nobody knows about.

    It is also close to self-limiting, which is why it is documented rather than closed.
    Being harmed by this needs somebody else to be holding the discarded copy, and that
    somebody imports the tool at module scope -- every test module in a session is imported
    during collection, before any test body runs, so the copy they hold has always been
    sampled by the time a loader replaces it. All three real incidents had that shape.
    Closing it properly means wrapping `importlib.util.module_from_spec` for the whole
    session, which is a stdlib patch in every worker, and is not worth it for a case that
    needs the victim and the culprit to be the same test.
    """
    inside_one_test = CULPRIT.replace(
        "def test_the_culprit_reads_a_constant_off_the_tool() -> None:\n",
        "def test_the_culprit_reads_a_constant_off_the_tool() -> None:\n"
        "    sys.path.insert(0, str(TOOLS))\n"
        "    import visibility_board  # noqa: F401\n",
    )

    child = child_run(tmp_path, {"test_only_one_file.py": inside_one_test})

    assert child.returncode == 0, (
        "the blind spot has closed, which is good news -- update this test and the note in "
        f"tests/module_identity.py:\n{child.stdout}\n{child.stderr}"
    )


def test_this_suites_conftest_installs_the_hooks_this_file_proves() -> None:
    """The child runs prove the hooks fail a run; this proves they are *this* run's hooks.

    Mutation: delete the import from `tests/conftest.py`. Every other test in this file
    still passes, because the child session builds a conftest of its own -- so without this
    the guard could be absent from the suite it was written for and nothing would say so.

    Compared by identity rather than by name: a conftest that defined its own
    ``pytest_runtest_call`` would satisfy a name check while running different code.
    """
    import conftest
    import module_identity

    for hook in ("pytest_runtest_call", "pytest_sessionfinish", "pytest_terminal_summary"):
        assert getattr(conftest, hook, None) is getattr(module_identity, hook), (
            f"tests/conftest.py does not re-export {hook} from module_identity, so the "
            "guard that tests/test_module_identity.py proves can fail is not the one this "
            "suite is running"
        )


def test_the_guard_raises_rather_than_returning_its_finding() -> None:
    """Mutation: have `refuse_a_second_copy` return its finding instead of raising.

    The child runs above would still be red, on the import error a broken conftest prints,
    so this names the exception type that the wiring actually depends on.
    """
    watcher = ModuleIdentity()
    modules: dict[str, object] = {"visibility_board": a_second_copy_of(SUBJECT, "b")}
    watcher.rebindings(modules)
    modules["visibility_board"] = a_second_copy_of(SUBJECT, "b")

    with pytest.raises(RebindingSeen):
        found = watcher.rebindings(modules)
        if found:
            raise RebindingSeen("\n\n".join(found))
