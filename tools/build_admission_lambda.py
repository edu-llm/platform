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
    "DEFAULT_PYTHON_PLATFORM",
    "DEFAULT_PYTHON_VERSION",
    "LambdaPackageError",
    "build_package",
    "main",
    "runtime_dependencies",
]

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Matches `Runtime: python3.12` and the function's default x86_64 architecture. Both are
#: declared in infra/admission-state-machine.yaml; changing either without changing these
#: produces a zip that imports on nothing.
DEFAULT_PYTHON_PLATFORM = "x86_64-manylinux_2_28"
DEFAULT_PYTHON_VERSION = "3.12"

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


def _copy_tree(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(*EXCLUDED_DIRECTORY_NAMES, "*.pyc", "*.pyo"),
    )


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
    python_platform: str = DEFAULT_PYTHON_PLATFORM,
    python_version: str = DEFAULT_PYTHON_VERSION,
) -> dict[str, object]:
    """Assemble the deployment zip and describe what went into it."""
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
        _copy_tree(package_source, staging / "edullm_platform")
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
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", required=True, type=Path, help="where to write the zip")
    parser.add_argument("--python-platform", default=DEFAULT_PYTHON_PLATFORM)
    parser.add_argument("--python-version", default=DEFAULT_PYTHON_VERSION)
    arguments = parser.parse_args(argv)

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
