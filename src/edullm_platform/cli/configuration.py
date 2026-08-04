"""Where the CLI reads the reviewed configuration, and why it must be the same files.

THE CLI AND THE VALIDATOR CANNOT BE ALLOWED TO DISAGREE ABOUT WHAT IS VALID. The compile
job reads ``config/*.yaml`` out of the platform checkout; admission reads its own copy out
of the Lambda zip and re-derives every verdict rather than believing the first one. A
third answer -- thresholds typed into the CLI, a dataset list embedded in a skill, a
profile table written into prose -- is the failure mode ``system-overview.md`` names under
"The agent layer": a restatement is wrong within a month and wrong silently, because
nothing tests prose against a threshold.

So this module resolves a directory rather than holding any values, and everything
downstream loads the same models the platform loads. Four sources, in order, each one an
answer to a different question:

1. ``--config-dir``, for a researcher checking a submission against a branch of the
   platform before it merges.
2. ``EDULLM_CONFIG_DIR``, the same thing without retyping it.
3. The copy packaged into the installed distribution, which is the ordinary path. An
   install from a tag pins the configuration to that tag -- which is reproducibility of the
   CLI and is *not* agreement with the platform, because ``submit-run.yml`` checks out
   ``github.sha`` on the default branch and admission runs whatever Lambda release is
   deployed. There are three vintages and this is the third. ``edullm submit`` names the
   current release before it dispatches, which is where that gap is made visible.
4. A ``config/`` directory found by walking up from the working directory, which is what
   makes the suite and a platform checkout work with no environment set at all.

The packaged copy is placed by ``force-include`` at wheel build time and is therefore
absent from an editable install, which is why the walk-up exists rather than being a
fallback nobody reaches.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from edullm_platform.config import load_yaml
from edullm_platform.contracts.dataset_registry import DatasetRegistry
from edullm_platform.contracts.image_scan import ImageScanExceptionRegistry
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.policy import ApprovalPolicy
from edullm_platform.contracts.repository_registry import RepositoryRegistry
from edullm_platform.contracts.workload import WorkloadCatalog

__all__ = [
    "CONFIG_DIRECTORY_VARIABLE",
    "ConfigurationUnreadableError",
    "ReviewedConfiguration",
    "find_config_directory",
    "load_reviewed_configuration",
]

CONFIG_DIRECTORY_VARIABLE = "EDULLM_CONFIG_DIR"

#: The file every candidate directory is tested for. Chosen because policy is the one file
#: no submission can be judged without, so a directory holding the others and not this one
#: is a partial checkout rather than a configuration.
SENTINEL_FILE = "policy.yaml"

#: Where ``force-include`` puts ``config/`` inside the built wheel.
PACKAGED_CONFIG_DIRECTORY = Path(__file__).resolve().parent.parent / "_config"


class ConfigurationUnreadableError(RuntimeError):
    """The reviewed configuration could not be found or could not be parsed.

    Separate from a refusal, and the workflow's own exit codes make the same separation for
    the same reason: a submission nobody could judge is not a submission anybody declined,
    and telling a researcher their spec is wrong when the platform's own files are missing
    sends them to edit the one thing that was fine.
    """


@dataclass(frozen=True)
class ReviewedConfiguration:
    """Every configuration file a local check needs, loaded once and passed around.

    Loaded together rather than lazily because the failure they share is the interesting
    one: a checkout with four of the six files is a broken installation, and finding that
    out one refusal at a time would report it as four unrelated problems.
    """

    directory: Path
    policy: ApprovalPolicy
    repositories: RepositoryRegistry
    catalog: WorkloadCatalog
    datasets: DatasetRegistry
    inventory: OrganizationInventory
    image_scan_exceptions: ImageScanExceptionRegistry


def find_config_directory(
    *,
    override: Path | None = None,
    environ: dict[str, str] | None = None,
    start: Path | None = None,
) -> Path:
    """The reviewed configuration this invocation will read, by the four routes above."""
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


def load_reviewed_configuration(directory: Path) -> ReviewedConfiguration:
    """Read the six files, or say which one could not be read."""
    try:
        return ReviewedConfiguration(
            directory=directory,
            policy=load_yaml(directory / "policy.yaml", ApprovalPolicy),
            repositories=load_yaml(directory / "repositories.yaml", RepositoryRegistry),
            catalog=load_yaml(directory / "workload-catalog.yaml", WorkloadCatalog),
            datasets=load_yaml(directory / "datasets.yaml", DatasetRegistry),
            inventory=load_yaml(directory / "organization.yaml", OrganizationInventory),
            image_scan_exceptions=load_yaml(
                directory / "image-exceptions.yaml", ImageScanExceptionRegistry
            ),
        )
    except (OSError, ValidationError, TypeError) as exc:
        raise ConfigurationUnreadableError(
            f"the reviewed configuration in {directory} could not be read: {exc}"
        ) from exc


def _require_config_directory(candidate: Path, described: str) -> Path:
    if (candidate / SENTINEL_FILE).is_file():
        return candidate
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
