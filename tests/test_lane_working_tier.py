"""Where a lane's files go, and the four numbers the lane reads rather than carries.

The layout is docs-frank/reference/system-overview.md's, under "Where data lives": the working
tier is a bucket of its own laid out <person>/<project>/, and the lane defaults its output there.
Every assertion below is about that shape rather than about a string, because the shape is what
a person navigating the bucket relies on and what the role's write fence is written against.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from edullm_platform.cli.lane import (
    SCRATCH_BUCKET,
    WorkingTierSettings,
    load_working_tier_settings,
    person_from_caller_arn,
    working_prefix,
    working_uri,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"

#: AWS's own documented example account. The one twelve-digit literal
#: tests/test_evidence.py's tree-wide scan exempts, so an ARN written here with any other
#: filler, zeroes included, fails that scan rather than this module.
EXAMPLE_ACCOUNT = "123456789012"


def test_the_prefix_is_the_person_alone_and_ends_with_a_separator() -> None:
    """Mutation: put a team segment back above it. Mutation: drop the trailing slash.

    The tier was <team>/<person>/ until 2026-08-05 and decisions.md records why it is not: the
    role's fence excepted edullm-scratch/*/${aws:SourceIdentity}/*, so the team was a wildcard and
    enforced nothing; seven people sit on two groups, so a lane would have had to resolve a team
    to know where to sync; and organization.yaml defines this tier as the work costed to nobody,
    which is the one thing a team segment would organise it by. A segment added back here also
    stops matching the role's excepted path, and every lane write is then denied naming nothing.

    The trailing slash is what makes an s3 prefix a directory to every tool that lists one, and
    without it "cai" also matches "caiiris".
    """
    assert working_prefix(person="caiiris") == "caiiris/"


def test_the_uri_names_the_bucket_the_overview_names() -> None:
    """Mutation: point it at the outputs bucket's teams/ prefix.

    decisions.md, under the working-tier entry, reverses an earlier position that working "does
    not need a bucket of its own". A prefix inside the outputs bucket carries no lifecycle rule
    of its own and is discoverable by nobody, and both of those are properties this slice needs.

    The outputs bucket is also the one place teams/<team>/ is right, which is why pointing here
    at it would be two mistakes rather than one. A run is charged to a group, grouped in Weights
    and Biases by it and routed to that group's lead; this tier is charged to nobody.
    """
    assert working_uri(person="caiiris", project="mixlaw") == (
        f"s3://{SCRATCH_BUCKET}/caiiris/mixlaw/"
    )


def test_the_person_is_recovered_from_a_broker_session() -> None:
    """Mutation: return the whole ARN, or the role name.

    The broker mints session names as broker-<person>-<epoch>, so the person is recoverable from
    the session segment. The role name is close and is the dashboard's spelling rather than the
    caller's, so a lane prefix built from it would not match what anybody expects to see.
    """
    arn = (
        f"arn:aws:sts::{EXAMPLE_ACCOUNT}:assumed-role/Intern-frank.gonzalez-sbsandbox"
        "/broker-frank.gonzalez-1785873426"
    )

    assert person_from_caller_arn(arn) == "frank.gonzalez"


def test_a_session_that_is_already_in_the_lane_yields_no_person() -> None:
    """THE CASE THAT DECIDES WHETHER A VERB CAN GUESS.
    Mutation: return the session name, which here is lane-mixlaw.

    A lane session is arn:.../assumed-role/edullm-researcher/lane-<project>, and the person is
    not in it: sts:GetCallerIdentity does not return the source identity. Returning "lane-mixlaw"
    would put every project's files under a person segment named after the project, which is a
    layout nobody could navigate and which the role's write fence would then refuse. None is the
    honest answer and Task 2 is where it becomes a sentence.
    """
    arn = f"arn:aws:sts::{EXAMPLE_ACCOUNT}:assumed-role/edullm-researcher/lane-mixlaw"

    assert person_from_caller_arn(arn) is None


def test_a_session_with_no_broker_prefix_still_yields_a_person() -> None:
    """Mutation: require the broker- prefix and the trailing epoch.

    Not every session is a broker session. A console session on an Intern role carries whatever
    name the console chose, and refusing there would refuse the person most likely to be trying
    this for the first time.
    """
    arn = f"arn:aws:sts::{EXAMPLE_ACCOUNT}:assumed-role/Intern-amy.lin-sbsandbox/console-session"

    assert person_from_caller_arn(arn) == "console-session"


def test_the_shipped_settings_load() -> None:
    """Mutation: leave config/reports/working-tier.yaml out of the tree.

    Every number the lane reads is in this file, so an absent one is two verbs that cannot decide
    how big a disk to ask for or how long to wait for a machine to answer.
    """
    settings = load_working_tier_settings(CONFIG_DIR)

    assert settings.object_expiry_days > 0
    assert settings.root_volume_gib > 0
    assert settings.boot_wait_seconds > 0
    assert settings.notebook_port > 1024


def test_a_notebook_port_in_the_privileged_range_is_refused() -> None:
    """Mutation: drop the field bound.

    Jupyter runs as an ordinary user on the instance, so a port below 1024 is one it cannot bind
    and the failure arrives as a port-forward that connects to nothing.
    """
    with pytest.raises(ValidationError):
        WorkingTierSettings.model_validate(
            {
                "schema_version": 1,
                "object_expiry_days": 90,
                "root_volume_gib": 200,
                "boot_wait_seconds": 600,
                "notebook_port": 80,
            }
        )
