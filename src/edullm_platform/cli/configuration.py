"""The eight files a local check loads, and where the CLI gets its directory from.

The finding moved to :mod:`edullm_platform.reviewed_configuration` and is re-exported here,
because it is not a CLI concern and pretending it was is part of how two shipped verbs came
to name a configuration file by a path of their own. ``researcher_lane.py`` is carried by the
janitor's Lambda zip and cannot import this module without pulling the whole CLI into it, so
while the resolver lived here there was no resolver for it to reach for and it wrote
``config/reports/researcher-lane.yaml`` instead. Read that module's header for the rule and
for the four sources.

What is still this module's own is the set: which files a local check needs, loaded together,
and the one that is a measurement rather than a rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from edullm_platform.contracts.dataset_registry import DatasetRegistry
from edullm_platform.contracts.image_scan import ImageScanExceptionRegistry
from edullm_platform.contracts.image_tokenizers import ImageTokenizerRecord
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.policy import ApprovalPolicy
from edullm_platform.contracts.repository_registry import RepositoryRegistry
from edullm_platform.contracts.resume_evidence import (
    NO_RESUME_DEMONSTRATIONS,
    ResumeDemonstrations,
)
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.reviewed_configuration import (
    CONFIG_DIRECTORY_VARIABLE,
    PACKAGED_CONFIG_DIRECTORY,
    SENTINEL_FILE,
    ConfigFile,
    ConfigurationUnreadableError,
    find_config_directory,
    load_config_file,
)
from edullm_platform.run_history import RunHistory, RunHistoryFormatError, load_run_history

__all__ = [
    "CONFIG_DIRECTORY_VARIABLE",
    "PACKAGED_CONFIG_DIRECTORY",
    "SENTINEL_FILE",
    "ConfigurationUnreadableError",
    "ReviewedConfiguration",
    "find_config_directory",
    "load_reviewed_configuration",
]


@dataclass(frozen=True)
class ReviewedConfiguration:
    """Every configuration file a local check needs, loaded once and passed around.

    Loaded together rather than lazily because the failure they share is the interesting
    one: a checkout with five of the seven files is a broken installation, and finding that
    out one refusal at a time would report it as several unrelated problems.

    ``directory`` is on it for a second reason now. Anything that needs an eighth file --
    the lane needs two under ``reports/`` -- reads it from here rather than resolving its
    own, so one invocation cannot answer out of two installs.
    """

    directory: Path
    policy: ApprovalPolicy
    repositories: RepositoryRegistry
    catalog: WorkloadCatalog
    datasets: DatasetRegistry
    inventory: OrganizationInventory
    image_scan_exceptions: ImageScanExceptionRegistry
    #: Which tokenizers each published training image was measured to hold. A rule rather
    #: than a measurement for loading purposes, which is a distinction worth being careful
    #: about: it *is* a measurement, and it is not optional, because the verdict it decides
    #: is the one field in this tree that can lose somebody a GPU allocation. An install
    #: missing it must not fall back to answering out of the platform's own tokenizer map,
    #: which is the derivation that marked three corpora runnable that no image can train.
    image_tokenizers: ImageTokenizerRecord
    #: What runs of each shape have actually taken, and ``None`` where this install carries
    #: no reading. The eighth file, and the only optional one, because it is the only one
    #: that is a measurement rather than a rule. A missing rule is a broken installation and
    #: a missing measurement is a thing to say out loud, which
    #: :data:`~edullm_platform.run_history.NO_HISTORY_PACKAGED` is.
    run_history: RunHistory | None = None
    #: Which repositories have been watched resuming a checkpoint. The eighth file, and the
    #: second measurement rather than rule, so it is defaulted the same way ``run_history``
    #: is -- except that its default is fail-closed rather than silent, because an absent
    #: measurement here refuses a second attempt where an absent duration only declines to
    #: print one.
    resume_demonstrations: ResumeDemonstrations = NO_RESUME_DEMONSTRATIONS


def load_resume_demonstrations(directory: Path) -> ResumeDemonstrations:
    """The demonstrations this install carries, or none where the file is not there.

    Absent rather than fatal, the way ``run-history.json`` is absent rather than fatal, and
    for the same reason: it is a measurement rather than a rule, and a directory holding
    every rule and no measurement is an old install or a directory a test built rather than
    a broken one.

    **AND THE EMPTY ANSWER IS THE FAIL-CLOSED ONE, WHICH IS WHY THIS DIFFERS FROM AN ABSENT
    HISTORY IN CONSEQUENCE IF NOT IN SHAPE.** A missing duration declines to print a
    sentence. A missing demonstration refuses a second attempt, which is the direction that
    costs a submitter one flag rather than costing them a retry they thought they had. A
    file that will not *parse* is still fatal, because a measurement this tree cannot read
    is a broken install rather than an absent measurement.
    """
    if not (directory / ConfigFile.RESUME_DEMONSTRATIONS.value).exists():
        return NO_RESUME_DEMONSTRATIONS
    return load_config_file(
        ConfigFile.RESUME_DEMONSTRATIONS, ResumeDemonstrations, directory=directory
    )


def load_reviewed_configuration(directory: Path) -> ReviewedConfiguration:
    """Read the seven rules and the one reading, or say which of them could not be read.

    The reading is optional and the seven are not. A directory holding five of the seven is
    a broken installation, and one holding no ``run-history.json`` is an ordinary install from
    before the first reading was committed, an editable checkout, or a directory a test
    built. What is not tolerated is a reading that will not parse, which
    :func:`~edullm_platform.run_history.load_run_history` raises on: a measurement this tree
    cannot read is a broken install rather than an absent measurement.
    """
    try:
        return ReviewedConfiguration(
            directory=directory,
            policy=load_config_file(ConfigFile.POLICY, ApprovalPolicy, directory=directory),
            repositories=load_config_file(
                ConfigFile.REPOSITORIES, RepositoryRegistry, directory=directory
            ),
            catalog=load_config_file(
                ConfigFile.WORKLOAD_CATALOG, WorkloadCatalog, directory=directory
            ),
            datasets=load_config_file(ConfigFile.DATASETS, DatasetRegistry, directory=directory),
            inventory=load_config_file(
                ConfigFile.ORGANIZATION, OrganizationInventory, directory=directory
            ),
            image_scan_exceptions=load_config_file(
                ConfigFile.IMAGE_EXCEPTIONS, ImageScanExceptionRegistry, directory=directory
            ),
            image_tokenizers=load_config_file(
                ConfigFile.IMAGE_TOKENIZERS, ImageTokenizerRecord, directory=directory
            ),
            run_history=load_run_history(directory),
            resume_demonstrations=load_resume_demonstrations(directory),
        )
    except (
        OSError,
        ValidationError,
        TypeError,
        RunHistoryFormatError,
        # ``load_config_file`` already reports one unreadable file as this class, so that a
        # lane verb reading an eighth file exits 2 rather than printing a traceback. Caught
        # and restated rather than let through, because the seven are loaded together on
        # purpose: what a person needs to be told is that this installation's configuration
        # is broken, and the directory is the part of that they can act on.
        ConfigurationUnreadableError,
    ) as exc:
        raise ConfigurationUnreadableError(
            f"the reviewed configuration in {directory} could not be read: {exc}"
        ) from exc
