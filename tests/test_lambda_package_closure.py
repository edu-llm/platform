"""What each Lambda zip carries, and why carrying less is safe.

The zip used to hold all 66 modules under ``src/edullm_platform``, so the release digest
moved for any change to any of them -- four times in one session, each on a module neither
handler imports. Each move costs a rebuild, an upload, a template edit and a bundle
regeneration that now takes over half an hour.

``infra/admission-validator-release.yaml`` named the fix before it was made -- *package
less, rather than check less* -- and warned against the wrong version of it: deciding
reachability by reading imports and hoping the reading is right. So the build does not
decide. It imports the entrypoint in a clean interpreter and asks Python which modules that
loaded.

**These tests are the second opinion on that measurement, and they are the stronger one.**
The build measures what was imported *at module load*; this checks that the resulting set is
closed under the whole import graph, including imports written inside functions, which the
runtime measurement cannot see because they have not run.

The zips cannot be imported here to check directly. They are built for
``x86_64-manylinux_2_28`` on purpose -- pydantic ships a compiled ``pydantic-core``, and a
zip assembled for this laptop would carry a ``.dylib`` and fail in Lambda as a missing
module rather than as an architecture. So the check is over the module set rather than over
a running import.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tools.build_admission_lambda import (
    ADMISSION_ENTRYPOINT,
    PACKAGE_DIRECTORY,
    reachable_modules,
)
from tools.build_lifecycle_lambda import RECORDER_ENTRYPOINT

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / PACKAGE_DIRECTORY

ENTRYPOINTS = (ADMISSION_ENTRYPOINT, RECORDER_ENTRYPOINT)


def module_name_of(relative: Path) -> str:
    parts = relative.with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(("edullm_platform", *parts))


def path_of(module: str) -> Path | None:
    """Where a dotted ``edullm_platform`` name lives, or None if it is not a module.

    ``from edullm_platform.contracts.base import ContractModel`` names a module; ``from
    edullm_platform.contracts.base import ContractModel`` with ``ContractModel`` resolved as
    a submodule would not. Both spellings are tried, because an import statement does not
    say which it meant.
    """
    relative = Path(*module.split(".")[1:])
    for candidate in (relative.with_suffix(".py"), relative / "__init__.py"):
        if (PACKAGE_ROOT / candidate).is_file():
            return candidate
    return None


def platform_imports(source: Path) -> set[str]:
    """Every ``edullm_platform`` module this file imports, wherever the import is written.

    Function-level imports are walked as well as top-level ones, and that is the point.
    The build's measurement runs the module and reads ``sys.modules``, so an import inside
    a function that has not been called is invisible to it -- and would be missing from the
    zip until the first invocation that took that branch.
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names if alias.name.startswith("edullm"))
        elif isinstance(node, ast.ImportFrom) and node.module:
            if not node.module.startswith("edullm_platform"):
                continue
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


@pytest.mark.parametrize("entrypoint", ENTRYPOINTS)
def test_the_packaged_module_set_is_closed_under_its_own_imports(entrypoint: str) -> None:
    """Mutation: drop a module from what the build packages.

    The check the runtime measurement cannot make on itself. If a packaged module imports
    something that is not packaged, the function raises ImportError at cold start -- which
    is loud and quick, and still worse than finding it here.

    Walks imports written inside functions too, which is where the runtime measurement is
    genuinely blind: a lazily imported module is not in ``sys.modules`` until the branch
    that imports it runs, which in a Lambda may be the first invocation of a rare path.
    """
    packaged = set(reachable_modules(PROJECT_ROOT, entrypoint))
    names = {module_name_of(member) for member in packaged}

    missing: dict[str, set[str]] = {}
    for member in sorted(packaged):
        for imported in sorted(platform_imports(PACKAGE_ROOT / member)):
            if imported in names:
                continue
            resolved = path_of(imported)
            if resolved is None or resolved in packaged:
                continue
            missing.setdefault(module_name_of(member), set()).add(imported)

    assert not missing, (
        f"{entrypoint}'s package imports modules it does not carry, so the function would "
        f"fail at import: {missing}"
    )


@pytest.mark.parametrize("entrypoint", ENTRYPOINTS)
def test_the_package_carries_less_than_the_whole_tree(entrypoint: str) -> None:
    """Mutation: revert to copying every module.

    The narrowing is the change, so it has to be observable. Without this the build could
    quietly go back to packaging everything and the only symptom would be release churn,
    which is exactly the symptom nobody attributed to a cause for four rounds of it.
    """
    packaged = reachable_modules(PROJECT_ROOT, entrypoint)
    everything = [
        path
        for path in PACKAGE_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
    ]

    assert len(packaged) < len(everything)
    assert len(packaged) >= 8, "a handful of modules means the measurement failed"


def test_the_two_handlers_carry_different_things() -> None:
    """Mutation: give both builders one shared entrypoint.

    A default that applied to both would put each function's dependencies into the other's
    release, which reintroduces the churn in a less obvious form: the recorder's digest
    would move whenever the validator started importing something.
    """
    validator = set(reachable_modules(PROJECT_ROOT, ADMISSION_ENTRYPOINT))
    recorder = set(reachable_modules(PROJECT_ROOT, RECORDER_ENTRYPOINT))

    assert validator != recorder
    assert recorder < validator or validator - recorder, (
        "the two handlers reach different parts of the package, and the release digests "
        "should move independently"
    )


def test_no_module_either_handler_carries_reaches_a_phase_specific_one() -> None:
    """Mutation: import a phase evidence or criteria module from a handler.

    Neither Lambda has any business reading a criteria definition or a proof generator, and
    an import that made it so would put the whole gate apparatus into a function that
    admits runs. It would also put the churn straight back: those modules change constantly.
    """
    for entrypoint in ENTRYPOINTS:
        carried = {module_name_of(member) for member in reachable_modules(PROJECT_ROOT, entrypoint)}
        unwanted = sorted(
            name
            for name in carried
            if any(
                part in name
                for part in ("criteria", "proof", "gate", "capture", "evidence", "checkpoints")
            )
        )
        assert unwanted == [], f"{entrypoint} carries {unwanted}"


@pytest.mark.parametrize("entrypoint", ENTRYPOINTS)
def test_nothing_a_handler_carries_imports_a_module_by_name_at_runtime(entrypoint: str) -> None:
    """The one thing neither measurement can see, asserted so it stays out of the zips.

    Mutation: add ``importlib.import_module("edullm_platform.something")`` to a module a
    handler carries. The load-time measurement would not see it, because the line has not
    run; the AST closure check above would not see it, because the module name is a string.
    It would be missing from the zip with no signal at all until the line executed -- in
    production, on whichever invocation took that branch.

    Scoped to what each handler carries rather than to the whole package, deliberately.
    ``proof_bundle.py`` does exactly this, legitimately: it imports contract modules by name
    to build the schema report, and it is not in either zip. Refusing it everywhere would be
    refusing a pattern that is fine where it lives.
    """
    offenders = [
        member.as_posix()
        for member in sorted(reachable_modules(PROJECT_ROOT, entrypoint))
        if any(
            marker in (PACKAGE_ROOT / member).read_text(encoding="utf-8")
            for marker in ("import_module", "__import__")
        )
    ]

    assert offenders == [], (
        f"{entrypoint} carries modules that import by name at runtime, which no static or "
        f"load-time measurement can follow: {offenders}"
    )
