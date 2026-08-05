"""One vocabulary for a refusal, whichever side of the submission path raises it.

Two places refuse a submission. ``edullm check`` refuses on a laptop and puts a code on
every refusal, because a code is what a skill and a test match on and an English sentence
stops matching the moment somebody rewords it. The compile step in CI refuses the same
things and used to raise prose and nothing else, so anything wanting to react to a refusal
could react to the first and not to the second.

**THE PART WORTH A TEST IS NOT THAT THE CODES EXIST. IT IS THAT THERE IS ONE SET OF THEM.**
``cli/preflight.py`` carries a docstring saying a second spelling of a rule is a second
answer to a settled question, and that is exactly what a code invented on one side and
retyped on the other would be. The two would agree the day they were written and disagree
the first time only one was corrected, and the direction that fails is the expensive one:
the CLI clears a submission, a lead releases it, and the compile step refuses it under a
name nothing recognises.

**NEITHER TEST BELOW RESTATES WHAT IT CHECKS, AND THAT IS THE WHOLE OF WHY THEY ARE HERE.**
A test listing the sixteen raise sites is green on the seventeenth, which is the one that
will be added without a code. So :func:`raise_sites` walks the package, parses each module
and resolves every raised name against that module's own namespace, and what it finds is
whatever the source says today. A seventeenth ``raise SubmissionRefusedError(...)`` joins the
population by being written, and fails because the base class carries no code to inherit.
:func:`test_the_base_refusal_carries_no_code_of_its_own` is what stops that being defeated by
giving the base a default.

The same argument holds for the second one. It reads the codes off the classes and the
literals out of ``preflight.py``, and asserts the two sets do not meet, so retyping any code
there is red without anybody having listed which codes exist. The direction that would make
it vacuous is preflight dropping the shared codes altogether, and
:func:`test_the_preflight_reads_its_shared_codes_off_the_exceptions` covers it.

``tests/test_release_tag_workflow.py`` derives the release trigger from the import graph for
the same reason rather than restating it, and its docstring records what a hand-written list
cost when somebody added an import.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import edullm_platform
from edullm_platform.cli import preflight
from edullm_platform.errors import SubmissionRefusedError

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: The attribute the code is carried on, spelled once. ``ComputeProfileResolutionError`` in
#: ``contracts/workload.py`` is where the pattern comes from and uses the same name.
REASON_CODE = "reason_code"


@dataclass(frozen=True)
class RaiseSite:
    """One ``raise`` of a submission refusal, as the source spells it."""

    path: str
    line: int
    raised: type[SubmissionRefusedError]

    @property
    def code(self) -> str:
        """What this refusal is known by, or the empty string when it is known by nothing.

        ``getattr`` rather than ``__dict__``, because a subclass inheriting a code from a
        subclass is a real arrangement and reading only the class's own dictionary would
        report it as uncoded. The base carries an annotation and no value, so it is the one
        class this returns nothing for.
        """
        return getattr(self.raised, REASON_CODE, "")

    def __str__(self) -> str:
        return f"{self.path}:{self.line} raises {self.raised.__name__}"


def dotted_name(node: ast.expr) -> tuple[str, ...] | None:
    """``Foo`` and ``errors.Foo`` as their parts, and anything else as ``None``.

    Both spellings are in the tree: the five modules that refuse import the classes by name,
    and reading a name off a module is what a sixth would do.
    """
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return tuple(reversed(parts))


def resolve(module: ModuleType, parts: tuple[str, ...]) -> object:
    """Follow a dotted name through a module's own namespace, or return ``None``.

    Against the imported module rather than against the import statements, so an alias, a
    relative import and a re-export all resolve to the class they name. Four of the five
    modules that refuse import from ``.errors`` and one imports from
    ``edullm_platform.errors``, which is the kind of difference this must not care about.
    """
    found: object = module
    for part in parts:
        found = getattr(found, part, None)
        if found is None:
            return None
    return found


def package_modules() -> Iterator[tuple[ModuleType, ast.Module]]:
    """Every module of this distribution, imported and parsed."""
    names = [edullm_platform.__name__] + [
        found.name
        for found in pkgutil.walk_packages(
            edullm_platform.__path__, f"{edullm_platform.__name__}."
        )
    ]
    for name in names:
        module = importlib.import_module(name)
        source = getattr(module, "__file__", None)
        if source is None:
            continue
        yield module, ast.parse(Path(source).read_text(encoding="utf-8"))


def raise_sites() -> tuple[RaiseSite, ...]:
    """Every place in the package that refuses a submission, read out of the source."""
    found: list[RaiseSite] = []
    for module, tree in package_modules():
        path = Path(str(module.__file__)).resolve().relative_to(PROJECT_ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or node.exc is None:
                continue
            called = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            parts = dotted_name(called)
            if parts is None:
                continue
            raised = resolve(module, parts)
            if isinstance(raised, type) and issubclass(raised, SubmissionRefusedError):
                found.append(RaiseSite(path=path, line=node.lineno, raised=raised))
    return tuple(sorted(found, key=lambda site: (site.path, site.line)))


def preflight_tree() -> ast.Module:
    return ast.parse(Path(str(preflight.__file__)).read_text(encoding="utf-8"))


def string_literals(tree: ast.Module) -> frozenset[str]:
    """Every string the source writes out, docstrings and comments-as-strings included."""
    return frozenset(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


def codes_read_by_name(tree: ast.Module) -> frozenset[type[SubmissionRefusedError]]:
    """The classes this source reads a code off by naming them, as ``SomeError.reason_code``.

    ``type(exc).reason_code`` is deliberately not counted. It names no class, which is the
    point of it, so what it proves is covered by the raise sites instead.
    """
    read: set[type[SubmissionRefusedError]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or node.attr != REASON_CODE:
            continue
        parts = dotted_name(node.value)
        if parts is None:
            continue
        found = resolve(preflight, parts)
        if isinstance(found, type) and issubclass(found, SubmissionRefusedError):
            read.add(found)
    return frozenset(read)


def test_every_refusal_the_compile_step_raises_carries_a_code() -> None:
    """Mutation: raise ``SubmissionRefusedError`` itself anywhere in the package.

    The seventeenth raise site is the case. Nothing here counts to sixteen, so writing one
    adds it to the population and the assertion below is about it on the first run.
    """
    sites = raise_sites()

    assert sites, (
        "no submission refusal is raised anywhere in the package, which is a broken walk "
        "rather than a clean codebase"
    )
    uncoded = tuple(site for site in sites if not site.code)
    assert not uncoded, (
        "a refusal a skill cannot match on: "
        + "; ".join(str(site) for site in uncoded)
        + ". Raise a subclass from edullm_platform.errors that carries a reason_code, and "
        "name the code the way the ones beside it are named."
    )


def test_the_base_refusal_carries_no_code_of_its_own() -> None:
    """Mutation: give ``SubmissionRefusedError`` a ``reason_code`` with a value.

    Without this the test above is one edit away from passing on everything. A default on
    the base is inherited by every raise site that forgot to name itself, so every one of
    them would report a code and the code would be the same word for all of them.
    """
    assert not getattr(SubmissionRefusedError, REASON_CODE, ""), (
        "the base refusal carries a code, so a raise site that names nothing inherits one "
        "and reads as named"
    )


def test_the_preflight_spells_no_refusal_code_the_exceptions_own() -> None:
    """Mutation: write any of these codes back into ``preflight.py`` as a string.

    The failure this defends is the one that module's own docstring warns about. Two
    spellings of a code agree on the day they are written, and the day one is corrected the
    CLI and the compile step name one refusal two ways.
    """
    owned = {site.code for site in raise_sites() if site.code}

    assert owned, "no refusal carries a code, so this test is checking nothing"
    forked = sorted(owned & string_literals(preflight_tree()))
    assert not forked, (
        f"cli/preflight.py writes out {', '.join(forked)}, which the exceptions in "
        "edullm_platform.errors already define. Read the code off the class instead: "
        "type(exc).reason_code where the exception is caught, SomeError.reason_code where "
        "the check is made again locally."
    )


def test_the_preflight_reads_its_shared_codes_off_the_exceptions() -> None:
    """Mutation: replace a ``SomeError.reason_code`` read in ``preflight.py`` with nothing.

    The test above passes on a preflight that dropped the shared codes entirely, which is
    the other way the two sides stop agreeing. This asserts the reads are still there and
    still land on refusal classes.
    """
    read = codes_read_by_name(preflight_tree())

    assert read, (
        "cli/preflight.py reads no refusal code off the class that raises it, so the two "
        "sides no longer share a definition even though neither spells one"
    )
    raised = {site.raised for site in raise_sites()}
    stranded = sorted(cls.__name__ for cls in read - raised)
    assert not stranded, (
        f"cli/preflight.py reads a code off {', '.join(stranded)}, which nothing raises. A "
        "code with no raise site behind it is a vocabulary entry the compile step cannot "
        "produce."
    )
