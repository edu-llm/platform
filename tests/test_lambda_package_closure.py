"""What each Lambda zip carries, and why carrying less is safe.

The zip used to hold all 66 modules under ``src/edullm_platform``, so the release digest
moved for any change to any of them -- four times in one session, each on a module neither
handler imports. Each move costs a rebuild, an upload, a template edit and a bundle
regeneration that now takes over half an hour.

It also used to hold every file under ``config/``, with the same consequence and a worse
audience. Eight team leads hold CODEOWNERS approval on ``/config/**`` so that profiles,
workloads, roster and dataset changes can move without the owner -- and every one of those
changes moved both release digests and left
``test_the_released_zip_is_the_one_this_tree_builds`` red, which only somebody with AWS
credentials could clear. A lead could approve a change they could not land. So the
configuration is now named per handler too, and the two tests below hold each list to what
that handler's modules actually name.

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

from edullm_platform.reviewed_configuration import ConfigFile
from tools.build_admission_lambda import (
    ADMISSION_CONFIG,
    ADMISSION_ENTRYPOINT,
    PACKAGE_DIRECTORY,
    reachable_modules,
)
from tools.build_janitor_lambda import JANITOR_CONFIG, JANITOR_ENTRYPOINT
from tools.build_lifecycle_lambda import RECORDER_CONFIG, RECORDER_ENTRYPOINT
from tools.build_notifier_lambda import NOTIFIER_CONFIG, NOTIFIER_ENTRYPOINT

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / PACKAGE_DIRECTORY
CONFIG_ROOT = PROJECT_ROOT / "config"

ENTRYPOINTS = (
    ADMISSION_ENTRYPOINT,
    RECORDER_ENTRYPOINT,
    JANITOR_ENTRYPOINT,
    NOTIFIER_ENTRYPOINT,
)

#: What each builder declares it packages, beside the entrypoint it packages it for. The
#: pairing is the thing under test: an entrypoint whose modules read a file its own builder
#: does not carry is a function that fails on the invocation that takes that branch.
PACKAGED_CONFIG = (
    (ADMISSION_ENTRYPOINT, ADMISSION_CONFIG),
    (RECORDER_ENTRYPOINT, RECORDER_CONFIG),
    (JANITOR_ENTRYPOINT, JANITOR_CONFIG),
    (NOTIFIER_ENTRYPOINT, NOTIFIER_CONFIG),
)


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


def config_filenames_named_by(entrypoint: str) -> set[str]:
    """Every ``config/`` filename written as a literal in the modules this zip carries.

    Static rather than runtime, and for the same reason the import closure above is: the
    audit-hook measurement that produced these lists sees a file being opened only on a
    branch something ran. A config read inside a rarely taken branch -- an exception path,
    a feature nothing exercises yet -- is invisible to it, and would be missing from the
    zip until the invocation that took that branch, in production.

    Filtered to names that exist under ``config/``, because a handler naming a ``.yaml``
    is not necessarily naming configuration. ``manifest_helpers.py`` is in the validator's
    packaged set and names six fixture files; those live under ``fixtures/``, are read by
    tests rather than by the function, and are nothing this builder should ship.

    ``*.json`` as well as ``*.yaml``, because one of the reviewed files is not YAML.
    ``config/run-history.json`` is a reading of the account rather than a setting anybody
    types, which is why it is JSON, and the notifier reads it to tell a lead what runs of a
    shape have taken. A glob that saw only YAML would let that file be declared and unread,
    or read and undeclared, and neither direction would be caught by either test below.

    **TWO SPELLINGS ARE READ AND ONE MODULE IS SKIPPED, WHICH IS WHAT KEEPS THIS HONEST NOW
    THAT THERE IS A SHARED VOCABULARY.** ``edullm_platform.config`` defines
    :class:`ConfigFile`, is in all four closures, and names every reviewed file there without
    reading any of them. Counting it would report each of the four functions as reading all
    seven and demand that every builder package them, which is the churn these lists exist to
    end. So the module that declares the vocabulary is skipped -- derived from
    ``ConfigFile.__module__`` rather than written down, so moving the class moves the skip.

    Skipping it is only safe because the other spelling is counted. A handler written today
    names a file as ``ConfigFile.POLICY`` and carries no literal at all, so a reader that saw
    only literals would report a handler that reads configuration as reading none, and the
    other direction of this test would then demand its builder stop packaging what it needs.
    A member reference outside that module counts as exactly the file it stands for.
    """
    on_disk = {path.name for path in CONFIG_ROOT.glob("*.yaml")}
    on_disk |= {path.name for path in CONFIG_ROOT.glob("*.json")}
    named: set[str] = set()
    for member in reachable_modules(PROJECT_ROOT, entrypoint):
        if module_name_of(member) == ConfigFile.__module__:
            continue
        for node in ast.walk(ast.parse((PACKAGE_ROOT / member).read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and node.value in on_disk:
                named.add(node.value)
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == ConfigFile.__name__
                and node.attr in ConfigFile.__members__
            ):
                named.add(Path(ConfigFile[node.attr].value).name)
    return named & on_disk


@pytest.mark.parametrize(("entrypoint", "declared"), PACKAGED_CONFIG)
def test_every_config_file_a_handler_names_is_one_its_builder_packages(
    entrypoint: str, declared: frozenset[str]
) -> None:
    """THE TEST THIS NARROWING IS ONLY SAFE BECAUSE OF.
    Mutation: read a new file from ``config/`` in a handler and do not add it to the list.

    Packaging less is the fix; packaging less than a handler reads is a function that
    deploys, passes every test here, and then raises FileNotFoundError on the invocation
    that reaches the read. That is strictly worse than the churn being fixed, because the
    churn was loud and this would not be.

    So the builders' lists are not trusted. This walks every module each zip carries, takes
    every string literal that names a file under ``config/``, and requires the builder to
    be carrying it.
    """
    missing = sorted(config_filenames_named_by(entrypoint) - set(declared))

    assert missing == [], (
        f"{entrypoint} reads {missing} from config/ and its builder does not package them, "
        "so the deployed function would fail on the first invocation that reads one"
    )


@pytest.mark.parametrize(("entrypoint", "declared"), PACKAGED_CONFIG)
def test_no_builder_packages_config_its_handler_never_names(
    entrypoint: str, declared: frozenset[str]
) -> None:
    """Mutation: leave a config file in the list after the handler stops reading it.

    The other direction, and the one that decays quietly. A list that only ever grows ends
    up back at ``config/*.yaml`` one entry at a time, and every stale entry puts a file
    nobody reads back into the release digest -- which is the whole failure being fixed
    here, arriving slowly instead of all at once.
    """
    unread = sorted(set(declared) - config_filenames_named_by(entrypoint))

    assert unread == [], (
        f"{entrypoint}'s builder packages {unread} and no module it carries names them, so "
        "editing one of those files moves this function's release digest for nothing"
    )


def test_only_the_validator_carries_configuration() -> None:
    """Mutation: give the recorder or the janitor the validator's config list.

    Neither of the other two reads anything under ``config/``, which is what makes them immune
    to a roster or catalog edit. Handing either the validator's list would restore the coupling
    in the least visible way available: the zip would build, deploy and run correctly, and its
    release digest would move every time somebody edited a policy it never opens.

    The janitor reads two numbers from ``config/reports/researcher-lane.yaml``, and reads them
    through the environment rather than the zip -- ``infra/expiry-janitor.yaml`` carries them
    and ``tests/test_janitor_infrastructure.py`` holds them equal to the file.
    """
    assert RECORDER_CONFIG == frozenset()
    assert JANITOR_CONFIG == frozenset()
    assert ADMISSION_CONFIG > RECORDER_CONFIG


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


def test_no_two_handlers_carry_the_same_module_set() -> None:
    """Mutation: give two builders one shared entrypoint.

    A default that applied to more than one would put each function's dependencies into the
    others' releases, which reintroduces the churn in a less obvious form: one digest would
    move whenever another handler started importing something.
    """
    sets = {
        entrypoint: frozenset(reachable_modules(PROJECT_ROOT, entrypoint))
        for entrypoint in ENTRYPOINTS
    }

    assert len(set(sets.values())) == len(ENTRYPOINTS)


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


def test_the_notifier_carries_the_five_files_it_reads_and_no_others() -> None:
    """Mutation: give the notifier the validator's configuration list.

    The recorder reads nothing under config/ and is therefore immune to a roster edit. The
    notifier cannot be. It resolves a W&B account to a person through organization.yaml, a
    queue to a profile through execution-targets.yaml, and a profile to a rate through
    workload-catalog.yaml. The approval message adds two: policy.yaml, because the routing
    line quotes the bound under which nobody releases a run rather than remembering it, and
    run-history.json, because the median a shape has taken is what tells an expensive run
    that is correct from an expensive run that is a typo.

    Five rather than eight is what is left of the narrowing, and the two absences still earn
    it. Nothing the notifier carries reads datasets.yaml, repositories.yaml or
    image-exceptions.yaml, and a dataset registration should not move this function's
    release digest.
    """
    assert NOTIFIER_CONFIG == frozenset(
        {
            "organization.yaml",
            "workload-catalog.yaml",
            "execution-targets.yaml",
            "policy.yaml",
            "run-history.json",
        }
    )
    assert NOTIFIER_CONFIG != ADMISSION_CONFIG
    assert NOTIFIER_CONFIG != RECORDER_CONFIG
