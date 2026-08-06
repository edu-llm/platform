"""Which reviewed files exist, where they are, and the rule that a location is never typed.

**A CONFIGURATION FILE IS NAMED RELATIVE TO A DIRECTORY THAT IS RESOLVED, NEVER WRITTEN
DOWN.** Two shipped things had written one down and neither had anything to do with the
other. ``cli/lane.py`` held ``"config/reports/working-tier.yaml"``, a path resolved against
the working directory, so ``edullm run`` and ``edullm shell`` raised ``FileNotFoundError``
for everybody who was not standing in a platform checkout -- which is everybody either verb
is for. The notifier Lambda held ``/var/task/config`` while its builder packaged the same
files at ``/var/task/edullm_platform/config``, so every invocation it had ever received died
on ``organization.yaml``. One mistake with two spellings: a location asserted by a person,
which nothing can check against the place the file actually lands.

So the spelling and the finding are separated here. :class:`ConfigFile` is the only way to
name a reviewed file and carries no directory at all;
:func:`find_config_directory` is the only way to answer where they are; and
:func:`load_config_file` is the pair applied. A path becomes a thing this module builds
rather than a thing a caller types.

**THREE THINGS HOLD THAT, AND EACH CATCHES WHAT THE OTHERS CANNOT SEE.**

1. This type. mypy runs strict over the package, so
   ``load_config_file("config/reports/working-tier.yaml", ...)`` is not a program. That stops
   the ordinary mistake at the point somebody would make it.
2. ``tests/test_config_resolution.py`` walks the AST of every module in this package and
   fails on any string literal that is a path ending in one of these names. That
   stops the bypass, which is reaching past this module to ``load_yaml`` with a path of your
   own, and it fails wherever the suite runs because it reads source rather than opening
   files.
3. ``tests/test_cli_outside_a_checkout.py`` drives the CLI from a temporary directory with
   the configuration reachable only through the resolver. That catches the version neither
   of the first two can see, which is a path assembled at runtime out of pieces.

The blunter rule -- refuse every relative path to one of these files, at read time -- was
written first and thrown away, and it is worth saying why so nobody writes it again. A
relative path here is not always a mistake: ``tools/resolve_published_image.py --registry
config/repositories.yaml`` is a workflow step anchoring a path at the directory
``actions/checkout`` just filled, deliberately, which is what a command-line argument is
for. The distinction that matters is between a path a caller supplies and a path the package
carries, and a source rule can see that where a runtime one cannot.

**FOUR SOURCES FOR THE DIRECTORY, IN ORDER, EACH ANSWERING A DIFFERENT QUESTION.** The CLI
and the validator cannot be allowed to disagree about what is valid: the compile job reads
``config/*.yaml`` out of the platform checkout, admission reads its own copy out of the
Lambda zip and re-derives every verdict rather than believing the first one, and a third
answer -- thresholds typed into the CLI, a dataset list embedded in a skill -- is wrong
within a month and wrong silently. So nothing here holds a value; it resolves a directory
and the models do the rest.

1. An override passed in, for a researcher checking a submission against a branch of the
   platform before it merges.
2. :data:`CONFIG_DIRECTORY_VARIABLE`, the same thing without retyping it.
3. The copy packaged into the installed distribution, which is the ordinary path. An install
   from a tag pins the configuration to that tag -- reproducibility of the CLI, and *not*
   agreement with the platform, because ``submit-run.yml`` checks out ``github.sha`` on the
   default branch and admission runs whatever Lambda release is deployed. ``edullm submit``
   names the current release before it dispatches, which is where that gap is made visible.
4. A ``config/`` directory found by walking up from the working directory, which is what
   makes the suite and a platform checkout work with no environment set at all.

The packaged copy is placed by ``force-include`` at wheel build time and is therefore absent
from an editable install, which is why the walk-up exists rather than being a fallback nobody
reaches.

**THE TWO LAMBDAS RESOLVE THEIR OWN DIRECTORY AND ARE RIGHT TO, WHICH IS WHY THIS IS NOT IN
``config.py``.** ``admission_handler`` and ``notifier_handler`` read the copy their zip
builder placed beside them, off ``__file__``, which is the same principle applied to a
different layout: the directory is derived from where the code actually landed rather than
asserted. They hand that directory to ``load_yaml`` and need none of the machinery here, and
``config.py`` is carried by three of the four Lambda zips -- so putting this there would
move three release digests, and every future edit to this vocabulary would move them again,
for a list those functions never read. ``tests/test_lambda_package_closure.py`` exists to
stop exactly that kind of coupling.
"""

from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ValidationError

from edullm_platform.config import load_yaml

__all__ = [
    "CONFIG_DIRECTORY_VARIABLE",
    "PACKAGED_CONFIG_DIRECTORY",
    "REVIEWED_FILENAMES",
    "SENTINEL_FILE",
    "ConfigFile",
    "ConfigurationUnreadableError",
    "find_config_directory",
    "load_config_file",
]

CONFIG_DIRECTORY_VARIABLE: Final = "EDULLM_CONFIG_DIR"

#: Where ``force-include`` puts ``config/`` inside the built wheel. Off this module's own
#: location rather than written out, for the reason the header gives.
PACKAGED_CONFIG_DIRECTORY: Final = Path(__file__).resolve().parent / "_config"


class ConfigFile(StrEnum):
    """Every reviewed configuration file, named by what it is rather than by where it is.

    **THE VALUES CARRY NO ``config/`` PREFIX AND THAT IS THE POINT.** A member is a name
    relative to whichever directory :func:`find_config_directory` resolved, so it is equally
    true of a platform checkout, of the copy inside a wheel at ``edullm_platform/_config``
    and of the copy inside a Lambda zip. A member that spelled a directory would be a path
    again, and would be wrong in two of those three places.

    ``tests/test_config_resolution.py`` holds this against the actual contents of ``config/``
    in both directions, so a file committed there without a member here fails, and a member
    here naming a file nobody committed fails too.
    """

    POLICY = "policy.yaml"
    REPOSITORIES = "repositories.yaml"
    WORKLOAD_CATALOG = "workload-catalog.yaml"
    DATASETS = "datasets.yaml"
    ORGANIZATION = "organization.yaml"
    IMAGE_EXCEPTIONS = "image-exceptions.yaml"
    EXECUTION_TARGETS = "execution-targets.yaml"
    CAPACITY = "capacity.yaml"
    ACCELERATORS = "accelerators.yaml"
    RUN_HISTORY = "run-history.json"
    ASKS = "reports/asks.yaml"
    CHECKPOINT_ACKNOWLEDGEMENTS = "reports/checkpoint-acknowledgements.yaml"
    LEAD_GATE = "reports/lead-gate.yaml"
    RESEARCHER_LANE = "reports/researcher-lane.yaml"
    STUDIO = "reports/studio.yaml"
    SURFACES = "reports/surfaces.yaml"
    WORKING_TIER = "reports/working-tier.yaml"


#: The file every candidate directory is tested for. Policy, because it is the one file no
#: submission can be judged without, so a directory holding the others and not this one is a
#: partial checkout rather than a configuration.
SENTINEL_FILE: Final = ConfigFile.POLICY.value

#: The last segment of every member above, which is how ``tests/test_config_resolution.py``
#: recognises a reviewed file inside a string literal. The last segment rather than the whole
#: member, because a literal spelling any directory in front of one of these names is the
#: thing being caught and ``reports/`` is only one of the prefixes it could carry.
REVIEWED_FILENAMES: Final[frozenset[str]] = frozenset(
    Path(member.value).name for member in ConfigFile
)


class ConfigurationUnreadableError(RuntimeError):
    """The reviewed configuration could not be found or could not be parsed.

    Separate from a refusal, and the workflow's own exit codes make the same separation for
    the same reason: a submission nobody could judge is not a submission anybody declined,
    and telling a researcher their spec is wrong when the platform's own files are missing
    sends them to edit the one thing that was fine.
    """


def find_config_directory(
    *,
    override: Path | None = None,
    environ: dict[str, str] | None = None,
    start: Path | None = None,
) -> Path:
    """The reviewed configuration this invocation will read, by the four routes above.

    Absolute in every case, the two a caller supplies included. An anchored directory is the
    whole product of this function: joined to a member, a relative one produces a path
    resolved against the working directory, which is where this started.
    """
    variables = os.environ if environ is None else environ
    if override is not None:
        return _require_config_directory(override, "the --config-dir given on the command line")
    from_environment = variables.get(CONFIG_DIRECTORY_VARIABLE)
    if from_environment:
        return _require_config_directory(
            Path(from_environment), f"the directory {CONFIG_DIRECTORY_VARIABLE} names"
        )
    if (PACKAGED_CONFIG_DIRECTORY / SENTINEL_FILE).is_file():
        return PACKAGED_CONFIG_DIRECTORY
    found = _walk_up_for_config(Path.cwd() if start is None else start)
    if found is not None:
        return found
    raise ConfigurationUnreadableError(
        "no reviewed configuration is in reach. edullm reads the same config/*.yaml the "
        "platform reads, so that what it refuses and what admission refuses cannot drift. "
        "An installed edullm carries its own copy; this one does not, which means it was "
        "installed from a source tree rather than from a built distribution. Point it at a "
        f"platform checkout with --config-dir, or set {CONFIG_DIRECTORY_VARIABLE}."
    )


def load_config_file[T: BaseModel](
    file: ConfigFile,
    model_type: type[T],
    *,
    directory: Path | None = None,
) -> T:
    """One reviewed file, from a directory the caller resolved or from the four sources.

    ``directory`` is the ordinary argument rather than the exception. Anything already
    holding a :class:`~edullm_platform.cli.configuration.ReviewedConfiguration` has the
    answer on it, and reading a second file from a second resolution is how two files that
    are meant to agree end up coming from two installs. ``None`` is for the callers with no
    configuration in hand, which is ``tools/enter_researcher_lane.py`` and nothing else on
    the shipped path.

    **AN UNREADABLE FILE LEAVES HERE AS A** :class:`ConfigurationUnreadableError` **AND NOT
    AS AN** ``OSError``. ``main`` turns this class into exit 2 and prints it, and lets
    anything else out as a traceback, which is the one thing this binary promises a
    researcher it will not do. It names the file and the directory rather than only the
    joined path, because the interesting half of "this install cannot read its
    configuration" is which configuration it thought it had.
    """
    root = find_config_directory() if directory is None else directory
    try:
        return load_yaml(root / file.value, model_type)
    except (OSError, ValidationError, TypeError) as exc:
        raise ConfigurationUnreadableError(
            f"{file.value} could not be read out of the reviewed configuration in {root}: {exc}"
        ) from exc


def _require_config_directory(candidate: Path, described: str) -> Path:
    if (candidate / SENTINEL_FILE).is_file():
        # ``absolute`` rather than ``resolve``: the join needs an anchor, nothing here needs
        # a symlink followed, and resolving would print a directory back to a researcher
        # that is not the one they typed.
        return candidate.absolute()
    raise ConfigurationUnreadableError(
        f"{described} holds no {SENTINEL_FILE}, so it is not a reviewed configuration "
        f"directory: {candidate}"
    )


def _walk_up_for_config(start: Path) -> Path | None:
    here = start.resolve()
    for directory in (here, *here.parents):
        candidate = directory / "config"
        if (candidate / SENTINEL_FILE).is_file():
            return candidate
    return None
