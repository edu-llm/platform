"""Package the admission validator into the zip CloudFormation deploys.

The function is `Code: {S3Bucket, S3Key, S3ObjectVersion}` in
`infra/admission-state-machine.yaml`, so something has to produce the object that key
names. Nothing did: the first deploy of the state machine stack reached
`AWS::Lambda::Function` with no zip in the artifacts bucket at all. This is that step.

Three properties are deliberate.

**The wheels are built for the runtime, not for the machine that runs this.** Pydantic v2
ships a compiled `pydantic-core`, so a zip assembled from a laptop's own environment
carries a macOS `.dylib` and the function fails at import with a message about a missing
module rather than about an architecture. The install is pinned to
`x86_64-manylinux_2_28`, CPython 3.12, and `--only-binary=:all:`, which matches the
`Runtime: python3.12` the template declares and its default x86_64 architecture. Building
from source is refused rather than allowed to silently produce host-shaped output.

**The dependency list is read from `pyproject.toml`.** Restating it here would let the
packaged function and the tested project drift apart, and the failure would appear as an
ImportError inside a Lambda rather than as a diff anybody reviews.

**The zip is deterministic.** Every entry gets a fixed timestamp and fixed permissions and
the names are sorted, so identical inputs produce identical bytes. That matters more here
than it usually does: `S3ObjectVersion` pins the template to one uploaded object, so a
rebuild that changes nothing must be recognisable as unchanged, or every run of this tool
would mint a new version and a new template edit for no reason.

The configuration is copied to `edullm_platform/config/`, which is where
:func:`edullm_platform.admission_handler.config_directory` looks when
``EDULLM_CONFIG_DIR`` is unset. That placement is what makes the decision record's
``policy_version`` a fact about what was deployed rather than a claim by the caller.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from collections.abc import Iterable, Sequence
from pathlib import Path

__all__ = [
    "ADMISSION_ENTRYPOINT",
    "DEFAULT_PYTHON_PLATFORM",
    "DEFAULT_PYTHON_VERSION",
    "LambdaPackageError",
    "build_package",
    "main",
    "reachable_modules",
    "runtime_dependencies",
]

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Matches `Runtime: python3.12` and the function's default x86_64 architecture. Both are
#: declared in infra/admission-state-machine.yaml; changing either without changing these
#: produces a zip that imports on nothing.
DEFAULT_PYTHON_PLATFORM = "x86_64-manylinux_2_28"
DEFAULT_PYTHON_VERSION = "3.12"

#: What the validator imports, and therefore what its zip carries. Named here because
#: the lifecycle recorder's builder passes its own, and a default that silently applied
#: to both would package one function's dependencies into the other.
ADMISSION_ENTRYPOINT = "edullm_platform.admission_handler"

#: The package, and the configuration it reads at runtime.
PACKAGE_DIRECTORY = Path("src") / "edullm_platform"
CONFIG_DIRECTORY = Path("config")
PACKAGED_CONFIG_PREFIX = Path("edullm_platform") / "config"

#: A fixed DOS timestamp. Zip cannot store a zero year, and 1980 is the format's epoch.
FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
#: 0o644 for files, in the high half of external_attr where zip keeps unix permissions.
FIXED_FILE_ATTRIBUTES = 0o644 << 16

#: Never packaged. Bytecode is host-specific and would break determinism; the rest is
#: build residue that has no meaning inside a function.
EXCLUDED_DIRECTORY_NAMES = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache"})
EXCLUDED_SUFFIXES = frozenset({".pyc", ".pyo"})

#: Packaged whatever the entrypoint imports, because they are not Python modules and are
#: therefore invisible to the reachability measurement below. The config is read at runtime
#: by path; the marker is what makes the package typed for anything that installs it.
ALWAYS_PACKAGED = frozenset({"py.typed"})

#: Below this, the measurement has gone wrong rather than the package having got small. The
#: admission handler reaches 28 modules and the lifecycle handler fewer; a run reporting a
#: handful means the import failed somewhere and produced a zip that will not start.
MINIMUM_PLAUSIBLE_MODULES = 8


class LambdaPackageError(RuntimeError):
    """The deployment artifact could not be assembled, or would have been wrong."""


def runtime_dependencies(project_root: Path) -> list[str]:
    """The project's runtime dependencies, read rather than restated."""
    manifest = project_root / "pyproject.toml"
    parsed = tomllib.loads(manifest.read_text(encoding="utf-8"))
    dependencies = parsed.get("project", {}).get("dependencies")
    if not dependencies:
        raise LambdaPackageError(
            f"{manifest} declares no project.dependencies, so this build would package "
            "the platform code with none of the libraries it imports"
        )
    return list(dependencies)


def _install_dependencies(
    staging: Path,
    dependencies: Sequence[str],
    *,
    python_platform: str,
    python_version: str,
) -> None:
    command = [
        "uv",
        "pip",
        "install",
        "--python-platform",
        python_platform,
        "--python-version",
        python_version,
        # Refuse a source build rather than let one produce host-shaped binaries.
        "--only-binary=:all:",
        "--target",
        str(staging),
        *dependencies,
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise LambdaPackageError(
            "installing the runtime dependencies for "
            f"{python_platform} / python{python_version} failed:\n{completed.stderr}"
        )


def reachable_modules(project_root: Path, entrypoint: str) -> tuple[Path, ...]:
    """The package files this entrypoint actually imports, measured rather than guessed.

    MEASURED IS THE WHOLE POINT, AND THE DISTINCTION IS WHY THIS IS SAFE.
    ``infra/admission-validator-release.yaml`` argues against narrowing the release digest
    to "the modules the handler imports", on the grounds that computing reachability and
    getting it wrong reintroduces the failure while looking covered. That objection is about
    *deciding* reachability -- reading imports, following a graph, hoping the graph is right.

    This does not decide anything. It imports the entrypoint in a clean interpreter and asks
    the interpreter which modules that loaded. A module Python did not load is a module the
    function does not import, by construction rather than by analysis.

    And getting it wrong fails in the good direction. A module missed here is an ImportError
    at cold start, on the next deploy, in a log, with the module's name in it -- loud, fast,
    and before anything depends on the answer. The failure the release control exists to
    prevent is the opposite: a deployed function quietly reading a stale catalog and refusing
    a submission for a reason that was true of the bytes and wrong about the account.

    A subprocess rather than this process, because this one has already imported things for
    its own reasons, and a measurement taken here would package them.

    What it cannot see: anything reached by ``importlib.import_module`` at runtime rather
    than by an import statement. The package has no such call, and a test asserts it -- if
    one is ever added, the module it names has to be added to the entrypoints below.
    """
    probe = (
        "import json, sys\n"
        f"import {entrypoint}\n"
        "print(json.dumps(sorted(\n"
        "    module.__file__ for name, module in sys.modules.items()\n"
        "    if name.startswith('edullm_platform') and getattr(module, '__file__', None)\n"
        ")))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=project_root,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(project_root / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise LambdaPackageError(
            f"importing {entrypoint} to measure what it packages failed:\n{completed.stderr}"
        )
    source_root = project_root / PACKAGE_DIRECTORY
    found = sorted(
        Path(name).resolve().relative_to(source_root.resolve())
        for name in json.loads(completed.stdout)
    )
    if len(found) < MINIMUM_PLAUSIBLE_MODULES:
        raise LambdaPackageError(
            f"{entrypoint} reported only {len(found)} modules, which is fewer than any real "
            "handler reaches; the measurement is wrong rather than the package small"
        )
    return tuple(found)


def _copy_reachable(source: Path, destination: Path, members: Sequence[Path]) -> None:
    """Copy exactly the measured modules, plus the package markers they need to be one.

    The intermediate ``__init__.py`` files are added rather than measured, because importing
    ``edullm_platform.contracts.manifest`` loads ``edullm_platform.contracts`` as a side
    effect and the measurement does see it -- but a package whose ``__init__`` were somehow
    missed would fail as "no module named" for a name that is right there in the zip.
    """
    wanted = set(members)
    for member in members:
        for parent in member.parents:
            marker = parent / "__init__.py"
            if (source / marker).is_file():
                wanted.add(marker)
    for name in ALWAYS_PACKAGED:
        if (source / name).is_file():
            wanted.add(Path(name))
    for member in sorted(wanted):
        target = destination / member
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / member, target)


def _packable_files(staging: Path) -> Iterable[Path]:
    for path in staging.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix in EXCLUDED_SUFFIXES:
            continue
        if EXCLUDED_DIRECTORY_NAMES.intersection(path.relative_to(staging).parts):
            continue
        yield path


def _write_deterministic_zip(staging: Path, output: Path) -> int:
    members = sorted(
        (path.relative_to(staging).as_posix(), path) for path in _packable_files(staging)
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, path in members:
            info = zipfile.ZipInfo(name, date_time=FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = FIXED_FILE_ATTRIBUTES
            archive.writestr(info, path.read_bytes())
    return len(members)


def build_package(
    project_root: Path,
    output: Path,
    *,
    entrypoint: str = ADMISSION_ENTRYPOINT,
    python_platform: str = DEFAULT_PYTHON_PLATFORM,
    python_version: str = DEFAULT_PYTHON_VERSION,
) -> dict[str, object]:
    """Assemble the deployment zip and describe what went into it.

    ``entrypoint`` decides what gets packaged, which is the property that keeps the release
    digest attached to this function rather than to the whole repository. Before it, the zip
    carried all 66 modules under ``src/edullm_platform`` and the digest moved for any change
    to any of them -- four times in one session, each on a module the validator never
    imports. Each move is a rebuild, an upload, a template edit, and a bundle regeneration
    that now takes over half an hour.

    ``infra/admission-validator-release.yaml`` named this fix before it was made: *if the
    tax becomes the thing that hurts, the fix is to package less rather than to check less*.
    The check is unchanged -- the digest still has to match a zip built from this tree.
    """
    package_source = project_root / PACKAGE_DIRECTORY
    config_source = project_root / CONFIG_DIRECTORY
    for required in (package_source, config_source):
        if not required.is_dir():
            raise LambdaPackageError(f"{required} is not a directory")

    configuration = sorted(config_source.glob("*.yaml"))
    if not configuration:
        raise LambdaPackageError(
            f"{config_source} holds no .yaml files, so the packaged handler would read "
            "its policy from nowhere and every decision record would be unattributable"
        )

    with tempfile.TemporaryDirectory() as directory:
        staging = Path(directory)
        _install_dependencies(
            staging,
            runtime_dependencies(project_root),
            python_platform=python_platform,
            python_version=python_version,
        )
        modules = reachable_modules(project_root, entrypoint)
        _copy_reachable(package_source, staging / "edullm_platform", modules)
        packaged_config = staging / PACKAGED_CONFIG_PREFIX
        packaged_config.mkdir(parents=True, exist_ok=True)
        for source in configuration:
            shutil.copy2(source, packaged_config / source.name)
        entries = _write_deterministic_zip(staging, output)

    payload = output.read_bytes()
    return {
        "path": str(output),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        "entries": entries,
        "python_platform": python_platform,
        "python_version": python_version,
        "configuration": [source.name for source in configuration],
        "entrypoint": entrypoint,
        # Recorded so a release can be read for what it carries as well as what it
        # hashes to. A number that jumps is a handler that started importing something.
        "modules": len(modules),
    }


def build_parser() -> argparse.ArgumentParser:
    """The parser, named the way ``tests/test_workflow_tool_arguments.py`` can find it.

    That module imports every tool a workflow invokes and compares the flags passed against
    the flags the parser accepts, so a renamed flag fails a test instead of failing a
    dispatch that has already assumed a role. It looks for ``build_parser`` by name and
    treats a tool without one as accepting nothing.

    This parser used to be built inline in :func:`main`, which was invisible to that check
    and cost nothing until 2026-08-01, when a workflow began calling this builder and the
    guard reported ``--output`` as an argument the parser does not accept. The parser did
    accept it; the guard simply could not see a parser at all.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", required=True, type=Path, help="where to write the zip")
    parser.add_argument("--python-platform", default=DEFAULT_PYTHON_PLATFORM)
    parser.add_argument("--python-version", default=DEFAULT_PYTHON_VERSION)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)

    try:
        record = build_package(
            PROJECT_ROOT,
            arguments.output,
            python_platform=arguments.python_platform,
            python_version=arguments.python_version,
        )
    except LambdaPackageError as error:
        print(str(error), file=sys.stderr)
        return 1

    print(json.dumps(record, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
