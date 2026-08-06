"""The two values the helper computes, which are the two a person would otherwise type wrong.

Neither needs AWS. The assume-role call itself is not tested here -- it is proved by the drill
against the real account, and a mocked STS would prove that the mock returns what it was told
to.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests.infrastructure_support import INFRA_ROOT
from tools.enter_researcher_lane import build_parser, expires_at, source_identity_from

#: AWS's own documentation account, which is what every other test module here spells a
#: fake account id as. tests/test_evidence.py refuses any other twelve-digit run in a
#: tracked file, because a real one committed once is committed for ever.
ACCOUNT_ID = "123456789012"


def test_the_source_identity_is_the_person_behind_the_broker_session() -> None:
    """Mutation: pass the whole ARN through.

    Source identity accepts 2-64 characters from [\\w+=,.@-] and must not start with "aws:",
    so an ARN is refused by STS and the refusal names the parameter rather than the value. The
    broker session name is the only place a person's name appears in the caller identity.
    """
    arn = (
        f"arn:aws:sts::{ACCOUNT_ID}:assumed-role/Intern-frank.gonzalez-sbsandbox/"
        "broker-frank.gonzalez-1785873426"
    )

    assert source_identity_from(arn) == "frank.gonzalez"


def test_a_session_name_with_no_broker_prefix_still_yields_something_usable() -> None:
    """Mutation: assume the broker- prefix and the trailing epoch are always there.

    Not every session is a broker session. A role assumed by another route carries whatever
    name the caller chose, and a helper that returned an empty string there would fail the
    trust policy's "?*" presence test with a message about a tag.
    """
    arn = f"arn:aws:sts::{ACCOUNT_ID}:assumed-role/Intern-amy.lin-sbsandbox/console-session"

    assert source_identity_from(arn) == "console-session"


def test_the_source_identity_agrees_with_the_prefix_the_working_tier_deny_fences() -> None:
    """THE ONE AGREEMENT NOTHING ELSE HOLDS, AND ITS FAILURE IS SILENT.
    Mutation: strip the dot, lowercase, or otherwise normalise the person's name here.

    infra/iam/researcher-role.yaml's seventh statement excepts
    arn:aws:s3:::edullm-work/*/${aws:SourceIdentity}/* and denies every other object write, so
    the string this function returns is literally the directory name a lane session may write
    into. The exploration route picks the same person out of the same caller ARN when it
    chooses that prefix. The day the two disagree, every write the lane makes is denied and the
    denial names no bucket, no key and no reason.

    Asserted against the tree's own template rather than against a literal, so a fence rewritten
    to some other variable fails here rather than at somebody's first upload.
    """
    template = (INFRA_ROOT / "iam" / "researcher-role.yaml").read_text(encoding="utf-8")
    arn = (
        f"arn:aws:sts::{ACCOUNT_ID}:assumed-role/Intern-frank.gonzalez-sbsandbox/"
        "broker-frank.gonzalez-1785873426"
    )

    assert "arn:aws:s3:::edullm-work/*/${aws:SourceIdentity}/*" in template
    assert source_identity_from(arn) == "frank.gonzalez"
    assert "/" not in source_identity_from(arn), (
        "a source identity carrying a slash would spread one person across two segments of "
        "the working tier and match the deny's single-segment exception in neither"
    )


#: Session names chosen so the two derivations' character classes disagree if they differ at
#: all. The first two are the ordinary cases and agreed before this corpus existed; the rest
#: are every character STS permits in a source identity that is not permitted in the segment
#: the working tier uses, plus one non-ASCII letter, which Python's ``\w`` keeps and an ASCII
#: class does not. A name is not exotic for containing an ``@`` or an accent.
SESSION_NAMES = (
    "broker-frank.gonzalez-1785873426",
    "console-session",
    "broker-frank+ops-1785873426",
    "broker-a,b-1785873426",
    "broker-x@y-1785873426",
    "broker-a=b-1785873426",
    "broker-jos\u00e9-1785873426",
    "broker-frank_gonzalez-1785873426",
)


@pytest.mark.parametrize("session", SESSION_NAMES)
def test_the_two_derivations_of_one_person_return_one_string(session: str) -> None:
    """THE SEAM BETWEEN TWO SLICES, AND IT WAS BROKEN FOR FIVE OF THESE EIGHT NAMES.
    Mutation: widen either character class back to what its own end permits.

    ``source_identity_from`` sets the value ``${aws:SourceIdentity}`` resolves to in the seventh
    statement's exception, and ``person_from_caller_arn`` picks the working-tier prefix a lane
    session writes to. Both derive a person from the same caller ARN, and the deny only lets a
    write through when the prefix the second returns is the segment the first named. They
    disagreed: STS permits ``+=,@`` and Python's ``\\w`` keeps non-ASCII letters, while an S3
    segment here is ``[A-Za-z0-9._-]``, so ``broker-frank+ops-1785873426`` yielded the identity
    ``frank+ops`` and the prefix ``frank-ops`` and every write that session made was denied.

    THE FAILURE IS SILENT AND THAT IS WHY THIS IS PARAMETRIZED RATHER THAN SPOT-CHECKED. An
    AccessDenied on a NotResource deny names no bucket, no key and no reason, so the person it
    happens to has nothing to search for and nothing to report beyond "the lane does not work".
    A single well-behaved name passes whatever the two functions do, which is what the check
    beside this one was doing on its own.

    Compared in the direction that matters. ``person_from_caller_arn`` returns None for a
    session already in the lane, which is a case the helper does not reach; wherever it names
    somebody, the helper must name the same person.
    """
    from edullm_platform.cli.lane import person_from_caller_arn

    arn = f"arn:aws:sts::{ACCOUNT_ID}:assumed-role/Intern-frank.gonzalez-sbsandbox/{session}"
    prefix = person_from_caller_arn(arn)

    assert prefix is not None, "this corpus holds no in-lane session"
    assert source_identity_from(arn) == prefix


def test_the_expiry_is_an_absolute_utc_timestamp() -> None:
    """Mutation: emit a duration, or a local time.

    aws-spend-controls.md, "The helper", gives two reasons this is absolute: LaunchTime is the
    wrong clock for a duration because a restarted instance keeps its original one, and
    extension is one unambiguous write where a duration has to be read and summed.
    """
    now = datetime(2026, 8, 4, 21, 30, 0, tzinfo=UTC)

    assert expires_at(now, 8) == "2026-08-05T05:30:00Z"


def test_a_lifetime_that_is_not_whole_hours_is_refused_by_the_parser() -> None:
    """Mutation: accept a float.

    A fractional lifetime is a timestamp with a minute component nobody typed, and the config
    file records the default in whole hours for the same reason.
    """
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--project", "mixlaw", "--lifetime-hours", "1.5"])


def test_the_parser_defaults_the_lifetime_from_configuration_and_not_from_a_literal() -> None:
    """Mutation: write the default into the argparse call.

    The number lives in config/reports/researcher-lane.yaml so that changing it is a reviewed
    diff. A literal here would be a second copy that silently disagrees with the janitor's.
    """
    from edullm_platform.researcher_lane import load_lane_settings

    parsed = build_parser().parse_args(["--project", "mixlaw"])

    assert parsed.lifetime_hours == load_lane_settings().default_lifetime_hours
