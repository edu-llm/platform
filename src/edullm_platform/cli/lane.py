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
from pathlib import Path
from typing import Final, Literal

from pydantic import Field

from edullm_platform.config import load_yaml
from edullm_platform.contracts.base import ContractModel

__all__ = [
    "LANE_INSTANCE_PROFILE",
    "WORKING_TIER_SETTINGS_PATH",
    "WORK_BUCKET",
    "WorkingTierSettings",
    "load_working_tier_settings",
    "person_from_caller_arn",
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
