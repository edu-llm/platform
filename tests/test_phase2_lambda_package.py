"""What the admission validator's deployment zip has to contain, and be.

The function is declared with `Code: {S3Bucket, S3Key, S3ObjectVersion}`, so the zip is a
deploy input rather than a build output nobody looks at. The first deploy of the state
machine stack failed with no zip in the bucket at all, which is the cheap version of this
failure; the expensive version is a zip that uploads cleanly and then fails at import
inside a function, where the only symptom is a Lambda error and the cause is a wheel built
for the wrong operating system.

So these assert the three things that cannot be seen by reading the file list: that the
compiled extension is a Linux x86_64 CPython 3.12 object, that the configuration lands
where the handler looks for it rather than merely somewhere in the archive, and that
identical inputs produce identical bytes -- which is what lets a rebuild that changed
nothing avoid minting a new S3 version and a new template edit.

The build runs `uv pip install` against an index, so it is marked slow and opted out of by
`-m "not slow"` along with everything else that starts a subprocess.
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from tools.build_admission_lambda import (
    DEFAULT_PYTHON_PLATFORM,
    DEFAULT_PYTHON_VERSION,
    LambdaPackageError,
    build_package,
    runtime_dependencies,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: What edullm_platform.admission_handler.config_directory reads when EDULLM_CONFIG_DIR is
#: unset. A file one directory away is a file the handler will not find.
PACKAGED_CONFIG_PREFIX = "edullm_platform/config/"


@pytest.fixture(scope="module")
def package(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    output = tmp_path_factory.mktemp("lambda") / "admission-validator.zip"
    return build_package(PROJECT_ROOT, output)


@pytest.fixture(scope="module")
def names(package: dict[str, object]) -> list[str]:
    with zipfile.ZipFile(str(package["path"])) as archive:
        return archive.namelist()


def test_the_dependency_list_is_the_project_s_own() -> None:
    # Restating it in the packaging tool is how a packaged function starts importing a
    # library the project no longer declares, or stops carrying one it added.
    declared = runtime_dependencies(PROJECT_ROOT)

    assert any(name.startswith("pydantic") for name in declared)
    assert any(name.lower().startswith("pyyaml") for name in declared)


def test_a_project_without_declared_dependencies_is_refused(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")

    with pytest.raises(LambdaPackageError, match="no project.dependencies"):
        runtime_dependencies(tmp_path)


@pytest.mark.slow
def test_the_handler_and_everything_it_imports_are_in_the_archive(names: list[str]) -> None:
    assert "edullm_platform/admission_handler.py" in names
    assert "edullm_platform/admission.py" in names
    assert "edullm_platform/canonical.py" in names
    assert "edullm_platform/contracts/admission.py" in names
    assert "edullm_platform/contracts/dataset_registry.py" in names


@pytest.mark.slow
def test_the_configuration_lands_where_the_handler_looks_for_it(names: list[str]) -> None:
    # The handler resolves config relative to its own __file__, so the four files it loads
    # have to sit inside the package rather than at the root of the archive.
    packaged = {name.removeprefix(PACKAGED_CONFIG_PREFIX) for name in names
                if name.startswith(PACKAGED_CONFIG_PREFIX)}

    assert {"policy.yaml", "organization.yaml", "workload-catalog.yaml", "datasets.yaml"} <= packaged


@pytest.mark.slow
def test_the_compiled_extension_is_built_for_the_lambda_runtime(names: list[str]) -> None:
    # The failure this prevents is silent on the machine that builds. Pydantic v2 ships a
    # compiled core, and a zip assembled from a macOS environment carries a .dylib that
    # imports nowhere -- reported inside Lambda as a missing module rather than as an
    # architecture mismatch.
    compiled = [name for name in names if name.endswith(".so")]

    assert compiled, "pydantic-core ships a compiled extension; none is in the archive"
    assert any("_pydantic_core" in name for name in compiled)
    for name in compiled:
        assert "x86_64-linux-gnu" in name, name
        assert "cpython-312" in name, name


@pytest.mark.slow
def test_nothing_host_specific_rides_along(names: list[str]) -> None:
    assert not [name for name in names if name.endswith((".pyc", ".pyo"))]
    assert not [name for name in names if "__pycache__" in name]
    assert not [name for name in names if name.endswith(".dylib")]


@pytest.mark.slow
def test_the_recorded_platform_matches_what_the_template_declares(
    package: dict[str, object],
) -> None:
    assert package["python_version"] == DEFAULT_PYTHON_VERSION == "3.12"
    assert package["python_platform"] == DEFAULT_PYTHON_PLATFORM
    assert "x86_64" in DEFAULT_PYTHON_PLATFORM


@pytest.mark.slow
def test_identical_inputs_produce_identical_bytes(tmp_path: Path) -> None:
    # S3ObjectVersion pins the template to one uploaded object, so a rebuild that changed
    # nothing has to be recognisable as unchanged. Without this, every build would mint a
    # new version and require a template edit that records no actual change.
    first = build_package(PROJECT_ROOT, tmp_path / "one.zip")
    second = build_package(PROJECT_ROOT, tmp_path / "two.zip")

    assert first["sha256"] == second["sha256"]
    assert (
        hashlib.sha256((tmp_path / "one.zip").read_bytes()).hexdigest()
        == hashlib.sha256((tmp_path / "two.zip").read_bytes()).hexdigest()
    )


@pytest.mark.slow
def test_a_missing_configuration_directory_is_refused_rather_than_packaged_empty(
    tmp_path: Path,
) -> None:
    # A zip with no policy in it deploys and runs, and every decision it makes cites a
    # policy version that came from nowhere. Failing the build is the cheaper outcome.
    (tmp_path / "src" / "edullm_platform").mkdir(parents=True)
    (tmp_path / "config").mkdir()

    with pytest.raises(LambdaPackageError, match="no .yaml files"):
        build_package(tmp_path, tmp_path / "out.zip")
