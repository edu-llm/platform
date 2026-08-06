"""One vocabulary for the reviewed files, and the rule that stops a second one appearing.

**WHAT THIS FILE IS FOR, SAID ONCE.** On 2026-08-06 two shipped things could not find their
own configuration and neither had anything to do with the other. ``cli/lane.py`` held
``"config/reports/working-tier.yaml"``, a path against the working directory, so
``edullm run`` and ``edullm shell`` raised ``FileNotFoundError`` for everybody outside a
platform checkout. The notifier Lambda held ``/var/task/config`` while its builder packaged
the same files at ``/var/task/edullm_platform/config``, so every invocation it had ever
received died on ``organization.yaml``. One mistake with two spellings: a location written
down by a person, which nothing can check against the place the file actually lands.

Three things now hold the rule, and this module carries two of them. The third is
``tests/test_cli_outside_a_checkout.py``.

- :class:`~edullm_platform.reviewed_configuration.ConfigFile` is the only way to name one of
  these files and carries no directory, and
  :func:`~edullm_platform.reviewed_configuration.load_config_file` is the only way
  to read one without supplying a directory yourself. mypy runs strict over the package, so
  passing a string where a member belongs is not a program. That is the half that stops the
  ordinary mistake where somebody would make it.
- :func:`test_no_module_writes_a_path_to_a_reviewed_configuration_file` is the half that
  stops the bypass, which is reaching past that function to ``load_yaml`` with a path of
  your own. It reads source and opens nothing, so it fails identically on a laptop, on a
  runner and inside a checkout -- which matters here more than usual, because the defect it
  exists for was invisible to 207 test modules for exactly one reason: they all ran from a
  platform checkout, where the wrong path resolved.

**WHY NOT A RUNTIME REFUSAL OF EVERY RELATIVE PATH**, which was written first and thrown
away. It is not true that a relative path to one of these files is always a mistake:
``uv run python tools/resolve_published_image.py --registry config/repositories.yaml`` is a
workflow step anchoring a path at the working directory ``actions/checkout`` just filled,
deliberately, which is what a command-line argument is for. A rule that cannot tell that
apart from a constant compiled into a verb refuses the interface in order to catch the bug.
The distinction that matters is between a path a caller supplies and a path the package
carries, and that distinction is exactly what a source rule can see and a runtime one
cannot.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from edullm_platform.reviewed_configuration import (
    CONFIG_DIRECTORY_VARIABLE,
    PACKAGED_CONFIG_DIRECTORY,
    REVIEWED_FILENAMES,
    SENTINEL_FILE,
    ConfigFile,
    ConfigurationUnreadableError,
    find_config_directory,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
PACKAGE_ROOT = PROJECT_ROOT / "src" / "edullm_platform"

#: The module that declares the vocabulary, which is the one place these names may be
#: written out. Derived rather than spelled, so moving the class moves the exemption with it
#: and cannot become a list.
VOCABULARY_MODULE = PACKAGE_ROOT / f"{ConfigFile.__module__.rsplit('.', 1)[-1]}.py"


def package_modules() -> list[Path]:
    return sorted(PACKAGE_ROOT.rglob("*.py"))


def spoken_strings(source: str) -> list[tuple[int, str]]:
    """Every string literal a module carries but the docstrings.

    Docstrings are exempt because this repository documents a file by naming it, at length,
    and a paragraph saying which file a rule lives in is not a read. The same exemption and
    the same reasoning as ``tests/test_cli_no_hardcoded_bounds.py``.
    """
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def is_a_path_to_reviewed_configuration(text: str) -> bool:
    """Whether a string is a written-down location of a reviewed configuration file.

    Three conditions together, and each one is there to keep this off something legitimate.

    It has to end in one of those names, because that is the subject. It has to carry
    a directory separator, because a bare ``"organization.yaml"`` is a name joined to a
    directory somebody resolved -- which is the correct pattern and is what
    ``notifications/facts.py`` deliberately does. And it has to hold no whitespace, because
    this package explains itself in prose: refusals say "is not in
    config/workload-catalog.yaml" to a researcher who then goes and edits that file, and a
    rule that flagged a sentence would be answered with an exemption list, which is where the
    next hardcoded path would hide.
    """
    if any(character.isspace() for character in text):
        return False
    if "/" not in text and "\\" not in text:
        return False
    return text.replace("\\", "/").rsplit("/", 1)[-1] in REVIEWED_FILENAMES


def test_the_rule_can_see_the_two_paths_that_shipped() -> None:
    """The tripwire's own tripwire, because a rule that matches nothing passes everything.

    The first two are verbatim what ``cli/lane.py`` and ``researcher_lane.py`` carried, and
    they are the whole reason this file exists. The rest are the shapes the same mistake
    would arrive in next: an absolute one, a Windows one, and one reached by climbing.
    """
    assert is_a_path_to_reviewed_configuration("config/reports/working-tier.yaml")
    assert is_a_path_to_reviewed_configuration("config/reports/researcher-lane.yaml")
    assert is_a_path_to_reviewed_configuration("/var/task/config/organization.yaml")
    assert is_a_path_to_reviewed_configuration("config\\policy.yaml")
    assert is_a_path_to_reviewed_configuration("../../config/run-history.json")


def test_the_rule_leaves_alone_what_is_not_a_written_down_location() -> None:
    """The other half, and the half that decides whether this survives its first false alarm.

    Every string here is real text from the package or a real call the package makes. A bare
    filename is the correct pattern and must pass; so must a sentence that names a file to
    the person reading a refusal, and so must a path to something that is not reviewed
    configuration at all.
    """
    assert not is_a_path_to_reviewed_configuration("organization.yaml")
    assert not is_a_path_to_reviewed_configuration("reports/")
    assert not is_a_path_to_reviewed_configuration(
        "is not in config/workload-catalog.yaml, so there is no instance type to start."
    )
    assert not is_a_path_to_reviewed_configuration(".edullm/run.yaml")
    assert not is_a_path_to_reviewed_configuration("fixtures/goldens/contract-models.json")


@pytest.mark.parametrize("module", package_modules(), ids=lambda path: path.name)
def test_no_module_writes_a_path_to_a_reviewed_configuration_file(module: Path) -> None:
    """THE RULE. Mutation: put ``"config/reports/working-tier.yaml"`` back in ``cli/lane.py``.

    A location written into the package is a claim about where a file will be at runtime,
    made by somebody who cannot know: the same source runs from a platform checkout, from a
    wheel where the files are at ``edullm_platform/_config``, and from a Lambda zip where
    they are beside the handler. At most one written-down path is right in all three, and
    both of the ones that shipped were right in none of the places anybody used them.
    """
    if module == VOCABULARY_MODULE:
        pytest.skip("the module that declares ConfigFile is where these names are spelled")

    written = [
        f"{module.relative_to(PROJECT_ROOT)}:{line}: {text!r}"
        for line, text in spoken_strings(module.read_text(encoding="utf-8"))
        if is_a_path_to_reviewed_configuration(text)
    ]

    assert not written, (
        "a reviewed configuration file is named by a path rather than resolved:\n  "
        + "\n  ".join(written)
        + "\nName it with a ConfigFile member and read it with load_config_file, which "
        "resolves the directory instead of asserting one."
    )


def test_the_package_is_actually_being_read() -> None:
    """Guards the glob: a moved package would make every case above vacuously pass."""
    names = {path.name for path in package_modules()}

    assert {"config.py", "researcher_lane.py", "notifier_handler.py"} <= names
    assert VOCABULARY_MODULE in package_modules()


def test_every_file_under_config_is_one_the_vocabulary_names() -> None:
    """Mutation: commit a fifteenth file under ``config/`` and name it nowhere.

    Both directions, because each one fails differently. A file on disk with no member is a
    file the next reader will name by hand, which is the whole subject of this module. A
    member with no file is a name that typechecks, resolves, and raises at the read.
    """
    on_disk = {
        path.relative_to(CONFIG_DIR).as_posix()
        for path in CONFIG_DIR.rglob("*")
        if path.is_file()
    }
    named = {member.value for member in ConfigFile}

    assert on_disk, "no configuration is committed at all, so this test asserts nothing"
    assert named == on_disk


def test_the_filenames_the_lambdas_spell_out_are_the_ones_the_vocabulary_holds() -> None:
    """Mutation: rename a file and leave one of the four restatements behind.

    Four modules spell a reviewed filename as a literal of their own, and every one of them
    is doing it deliberately. ``notifications/facts.py`` says why in its own header: the zip
    builder measures an entry point's import closure, and reaching for a shared constant
    would drag a module into a Lambda package to share a string. So the copies stay and this
    is what stops them drifting -- the same discipline that module already holds
    :data:`SUBMITTER_FIELD` to.
    """
    from edullm_platform.notifications.facts import (
        CATALOG_FILENAME,
        ORGANIZATION_FILENAME,
        TARGETS_FILENAME,
    )
    from edullm_platform.placement import CAPACITY_FILENAME
    from edullm_platform.run_history import HISTORY_FILENAME

    assert ORGANIZATION_FILENAME == ConfigFile.ORGANIZATION
    assert CATALOG_FILENAME == ConfigFile.WORKLOAD_CATALOG
    assert TARGETS_FILENAME == ConfigFile.EXECUTION_TARGETS
    assert CAPACITY_FILENAME == ConfigFile.CAPACITY
    assert HISTORY_FILENAME == ConfigFile.RUN_HISTORY
    assert SENTINEL_FILE == ConfigFile.POLICY


def test_every_directory_the_resolver_returns_is_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: hand back what was passed in.

    An anchored directory is the whole product of this function, and a relative one is the
    same guess in a longer coat: joined to a member it produces a path resolved against the
    working directory, which is where this started. The override and the variable are the two
    routes a person types by hand and therefore the two that can arrive relative.
    """
    monkeypatch.chdir(PROJECT_ROOT)

    assert find_config_directory(override=Path("config"), environ={}).is_absolute()
    assert find_config_directory(
        environ={CONFIG_DIRECTORY_VARIABLE: "config"}
    ).is_absolute()
    assert find_config_directory(environ={}, start=PROJECT_ROOT).is_absolute()
    assert PACKAGED_CONFIG_DIRECTORY.is_absolute()


def test_a_directory_without_the_sentinel_is_refused_rather_than_read(tmp_path: Path) -> None:
    """Mutation: accept any directory that was named.

    A directory holding some of these files and not ``policy.yaml`` is a partial checkout,
    and reading six files out of it one refusal at a time reports one broken installation as
    six unrelated problems.
    """
    with pytest.raises(ConfigurationUnreadableError) as raised:
        find_config_directory(override=tmp_path, environ={})

    assert SENTINEL_FILE in str(raised.value)
