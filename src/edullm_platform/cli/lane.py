"""What the exploration route decides without an account, which is nearly all of it.

THIS MODULE MAKES NO AWS CALL AND RUNS NO PROCESS. It answers where a person's files go, which
instance type a compute profile names, which tags a launch must carry, and the exact argv of
every command the two verbs run. main.py runs those commands. Keeping the decisions here is what
lets the whole lane be tested with no credential, which is the same arrangement cli/actions.py
already has with GitHub.

WHY THE LANE IS NOT THE SUBMISSION PATH, SAID HERE BECAUSE HERE IS WHERE SOMEBODY WOULD REACH
FOR IT. Nothing in this module imports cli/preflight.py's rules, and tests/test_lane_is_ungated.py
fails if a lane verb ever calls one.
docs-frank/superpowers/specs/2026-08-04-platform-buildout-design.md, under "The exploration route
is a slice, not a non-goal", names the reason: check refuses unregistered_repository anywhere
outside the five registered repositories, that lookup sits in run_preflight, and a second verb
calling run_preflight picks the refusal up for free. The lane is meant to be ungated. You get a
machine, you do what you like, nothing is checked and nothing is recorded as citable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from pydantic import Field

from edullm_platform.cli.configuration import (
    ConfigurationUnreadableError,
    ReviewedConfiguration,
)
from edullm_platform.cli.preflight import Refusal
from edullm_platform.config import load_yaml
from edullm_platform.contracts.base import ContractModel
from edullm_platform.placement import (
    CAPACITY_FILENAME,
    PLACES_AFTER_A_WAIT,
    PLACES_RELIABLY,
    UnreadableCapacityError,
    read_capacity,
)

__all__ = [
    "LANE_INSTANCE_PROFILE",
    "WORKING_TIER_SETTINGS_PATH",
    "WORK_BUCKET",
    "LaneRequest",
    "WorkingTierSettings",
    "instance_type_for",
    "lane_refusals",
    "load_working_tier_settings",
    "person_from_caller_arn",
    "placement_warning",
    "working_prefix",
    "working_uri",
]

#: The working tier. docs-frank/reference/system-overview.md, "Where data lives", draws it as a
#: bucket of its own rather than a prefix, for three reasons that hold separately: runs/* is
#: human-writable so a prefix inside the outputs bucket would be a naming convention and nothing
#: more, a bucket carries its own lifecycle rule, and a bucket is discoverable by name.
#:
#: NO sbsandbox-intern-edullm- PREFIX, and that has a consequence somebody will otherwise
#: rediscover at deploy time. sbsandbox-intern-edullm-infra-deployer scopes every S3 grant it
#: holds to arn:aws:s3:::sbsandbox-intern-edullm-*, so CI cannot create this bucket and
#: infra/work-bucket.yaml is applied by hand. The name is the overview's and a person types it.
WORK_BUCKET: Final = "edullm-work"

#: The instance profile the lane machine carries. infra/iam/lane-instance-role.yaml creates it,
#: and the launch passes it as ``--iam-instance-profile Name=<this>``. A rename on either side is
#: a launch that fails after a machine has already been priced.
LANE_INSTANCE_PROFILE: Final = "edullm-lane-instance"

WORKING_TIER_SETTINGS_PATH: Final = "config/reports/working-tier.yaml"

#: The session segment of an assumed-role ARN for a session this lane itself created. Sessions
#: are named lane-<project> by the verbs, so a caller matching this is already in the lane and
#: carries no recoverable person: sts:GetCallerIdentity does not return the source identity.
_ALREADY_IN_THE_LANE = re.compile(r"^lane-")

#: What is safe in a prefix segment and in a tag value. Everything else is replaced, so a person
#: or a project with an awkward character produces one prefix rather than two.
_UNSAFE_IN_A_SEGMENT = re.compile(r"[^A-Za-z0-9._-]")

_BROKER_PREFIX = re.compile(r"^broker-")
_TRAILING_EPOCH = re.compile(r"-\d+$")

#: How long a prefix segment may be. Short enough that a pathological session name cannot make a
#: key nobody can read, long enough that no real one is touched.
_SEGMENT_LIMIT: Final = 64


class WorkingTierSettings(ContractModel):
    schema_version: Literal[1]
    object_expiry_days: int = Field(gt=0)
    root_volume_gib: int = Field(gt=0)
    boot_wait_seconds: int = Field(gt=0)
    #: Above the privileged range, because Jupyter runs as an ordinary user on the instance and
    #: a port it cannot bind produces a forward that connects to nothing.
    notebook_port: int = Field(gt=1024, lt=65536)


def load_working_tier_settings(
    path: Path | str = WORKING_TIER_SETTINGS_PATH,
) -> WorkingTierSettings:
    return load_yaml(path, WorkingTierSettings)


def working_prefix(*, team: str, person: str) -> str:
    """Where one person's working set lives, as an S3 prefix ending in a separator.

    Team first, which is the overview's order and not a preference: a team's whole working set is
    then one listing under one prefix. The trailing separator is what makes this a directory to
    every tool that lists one, and without it a prefix search for one person also finds everybody
    whose name starts the same way.
    """
    return f"{_UNSAFE_IN_A_SEGMENT.sub('-', team)}/{_UNSAFE_IN_A_SEGMENT.sub('-', person)}/"


def working_uri(*, team: str, person: str, project: str) -> str:
    """The s3:// address one project's working set is synced to and from."""
    segment = _UNSAFE_IN_A_SEGMENT.sub("-", project)
    return f"s3://{WORK_BUCKET}/{working_prefix(team=team, person=person)}{segment}/"


def person_from_caller_arn(caller_arn: str) -> str | None:
    """Who is at the keyboard, read off an assumed-role ARN, or nothing when it cannot say.

    The broker mints session names as ``broker-<person>-<epoch>``, so the person is in the
    session segment and nowhere else in the identity. A session this lane created is named
    ``lane-<project>`` and carries no person at all, because ``sts:GetCallerIdentity`` does not
    return the source identity; None rather than a guess, so the verb can say what to do instead.

    Self-asserted either way. ``docs-frank/reference/aws-spend-controls.md``, "What the lane does
    not cover", records that nothing stops somebody passing another person's source identity, so
    this is attribution and a fence rather than authentication.
    """
    session = caller_arn.rsplit("/", 1)[-1]
    if _ALREADY_IN_THE_LANE.match(session):
        return None
    session = _BROKER_PREFIX.sub("", session)
    session = _TRAILING_EPOCH.sub("", session)
    cleaned = _UNSAFE_IN_A_SEGMENT.sub("-", session)[:_SEGMENT_LIMIT]
    return cleaned or None


@dataclass(frozen=True)
class LaneRequest:
    """What a lane verb was asked for, after the flags and the caller identity are merged.

    Four fields, against the fifteen a submission carries. That difference is the slice: a
    submission is a record and needs everything a record names, and a lane ask is a machine and a
    place to put files.
    """

    project: str
    team: str
    person: str
    compute_profile: str


def instance_type_for(configuration: ReviewedConfiguration, profile_name: str) -> str | None:
    """The EC2 instance type one compute profile names, or nothing where the catalog has none.

    A plain lookup rather than ``resolve_compute_profile_for_execution``, which is what the
    submission path calls, because that function also refuses an unprovisioned profile.
    ``provisioned`` means a Batch queue exists, a lane machine is not a Batch job, and importing
    that meaning here would refuse a shape the researcher role's own allow-list permits.
    """
    for profile in configuration.catalog.compute_profiles:
        if profile.name == profile_name:
            return profile.instance_type
    return None


def placement_warning(configuration: ReviewedConfiguration, profile_name: str) -> str | None:
    """What ``config/capacity.yaml`` says about finding this shape, or nothing where it is quiet.

    A sentence and never a refusal, which is the same choice ``system-overview.md`` records the
    compile step making under "The machines". The reasoning there transfers exactly: the person
    asking is the person paying and the wait is theirs to accept.

    **THE FILE IS READ HERE RATHER THAN OFF ``ReviewedConfiguration``, AND THAT IS TO KEEP A
    CORRECTION THIS REPOSITORY ALREADY PAID FOR.** ``load_reviewed_configuration`` opens six
    files, ``tests/test_release_tag_workflow.py`` derives the release trigger by watching it open
    them, and ``release-tag.yml``'s own header records what happened when capacity counted as
    reviewed configuration: ``v0.2.1`` announced that ``edullm check`` would answer differently
    when the only file that had moved was ``config/capacity.yaml``, and everybody who re-installed
    on that sentence did it for nothing. A seventh field here would reinstate that, so the reader
    and the verdicts are shared with the compile step and the loader is left alone.

    ``edullm_platform.placement`` owns both, so there is no second table of verdicts and no second
    parse. What is not shared is the rendering: that module's sentences are markdown for a pull
    request comment, and this one is a line above the expiry in somebody's terminal.
    """
    path = configuration.directory / CAPACITY_FILENAME
    try:
        capacity = read_capacity(path)
    except UnreadableCapacityError as exc:
        # Re-raised as the class ``main`` already turns into exit 2. ``read_capacity`` is right to
        # raise rather than default to "everything places", and a ValueError escaping a verb is a
        # traceback in front of a researcher, which is the one thing this binary promises not to
        # do. An unreadable capacity file is an unusable installation and reads as one.
        raise ConfigurationUnreadableError(
            f"{path} is not a document edullm can act on, so whether a machine is likely to "
            f"start cannot be said: {exc}"
        ) from exc
    verdict = next((record for record in capacity if record.profile == profile_name), None)
    if verdict is None or verdict.places == PLACES_RELIABLY:
        return None
    if verdict.places == PLACES_AFTER_A_WAIT:
        # ``read_capacity`` refuses this verdict without a wait, so there is always one to quote.
        assert verdict.wait is not None
        return (
            f"config/{CAPACITY_FILENAME} records {profile_name} as arriving after a wait, "
            f"measured by a {verdict.measured_by}. {verdict.wait} Ctrl-C stops waiting and "
            "starts nothing."
        )
    return (
        f"config/{CAPACITY_FILENAME} records {profile_name} as placing "
        f"{verdict.places}, measured by a {verdict.measured_by}. Starting it is allowed and it "
        "may take a while to arrive, or never arrive. Ctrl-C stops waiting and starts nothing."
    )


def lane_refusals(
    request: LaneRequest, *, configuration: ReviewedConfiguration
) -> tuple[Refusal, ...]:
    """Everything the lane refuses, which is four things and is the whole list.

    **NOTHING HERE IS A PERMISSION AND THAT IS THE TEST EVERY CANDIDATE HAS TO PASS.** Three of
    the four say a destination is misspelled, and the fourth says the caller cannot be named. Add
    a fifth only if the same is true of it, and read
    ``docs-frank/superpowers/specs/2026-08-04-platform-buildout-design.md`` under "The exploration
    route is a slice, not a non-goal" first.

    ``Refusal`` is imported from ``cli/preflight.py`` and no rule there is called. A frozen
    dataclass of two strings is a shape rather than a judgement, and a second refusal type would
    give the CLI two things to render. ``tests/test_lane_verdicts.py`` is where every refusal the
    submission path makes is ruled on one at a time, and it fails when a new one is added there
    without a ruling here.
    """
    refusals: list[Refusal] = []
    if not request.person:
        refusals.append(
            Refusal(
                code="cannot_tell_who_you_are",
                detail=(
                    "this session is already inside the lane, and sts:GetCallerIdentity does not "
                    "return the source identity, so which person's working prefix to use cannot "
                    "be read from it. Run this from your ordinary session and it enters the lane "
                    "itself, which is one command rather than two."
                ),
            )
        )
    if not request.project:
        refusals.append(
            Refusal(
                code="no_project",
                detail=(
                    "--project names what this machine is for. It tags the instance and the "
                    "volume, it is the last segment of the working prefix, and it is what cost "
                    "attribution reads. There is no default for it, because a default would put "
                    "two unrelated pieces of work under one name and one bill."
                ),
            )
        )
    declared = {team.team_id for team in configuration.inventory.team_bindings.teams}
    if request.team not in declared:
        refusals.append(
            Refusal(
                code="unknown_team",
                detail=(
                    f"{request.team!r} is not a team config/organization.yaml declares. "
                    f"Declared: {', '.join(sorted(declared))}. Team is the first segment of your "
                    "working prefix, so a name nothing declares puts your files where no listing "
                    "of any group's work will find them."
                ),
            )
        )
    if instance_type_for(configuration, request.compute_profile) is None:
        offered = ", ".join(
            sorted(profile.name for profile in configuration.catalog.compute_profiles)
        )
        refusals.append(
            Refusal(
                code="unknown_machine",
                detail=(
                    f"{request.compute_profile!r} is not in config/workload-catalog.yaml, so "
                    f"there is no instance type to start. Offered: {offered}. Unlike a "
                    "submission, an unprovisioned profile is fine here: provisioned means a "
                    "Batch queue exists and this is not a Batch job."
                ),
            )
        )
    return tuple(refusals)
