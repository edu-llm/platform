"""The launch, asserted as the argv it is, because that is the whole of what the account sees.

WHY THIS IS ARGV AND NOT A MOCKED CLIENT. A mocked boto3 proves the mock returned what it was
told to. The interesting content of a lane launch is which flags it carries: two tags in the
exact case the role's conditions use, an instance profile, a metadata option, a root volume size
read from configuration. Every one of those is visible in the argv and invisible in a mock's
return value.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from edullm_platform.cli.configuration import load_reviewed_configuration
from edullm_platform.cli.lane import (
    LANE_INSTANCE_PROFILE,
    LANE_TAG_KEY,
    LaneRequest,
    assume_lane_argv,
    credentials_environment,
    expires_at,
    find_machine_argv,
    instance_type_for,
    load_working_tier_settings,
    run_instances_argv,
)
from edullm_platform.researcher_lane import (
    EXPIRES_AT_TAG_KEY,
    GOVERNANCE_TAG_KEYS,
    PROJECT_TAG_KEY,
    RESEARCHER_ROLE_NAME,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETTINGS = load_working_tier_settings(PROJECT_ROOT / "config" / "reports" / "working-tier.yaml")
REQUEST = LaneRequest(
    project="mixlaw", team="memory-split", person="caiiris", compute_profile="gpu-1xt4"
)

#: The account id AWS reserves for documentation. Twelve zeroes would be rejected by
#: tests/test_evidence.py, which scans the tracked tree for anything shaped like a real one.
EXAMPLE_ACCOUNT = "123456789012"


def launch(*, spot: bool = False) -> tuple[str, ...]:
    return run_instances_argv(
        request=REQUEST,
        instance_type=instance_type_for(
            load_reviewed_configuration(PROJECT_ROOT / "config"), "gpu-1xt4"
        )
        or "",
        image_id="ami-000000000000000aa",
        subnet_id="subnet-000000000000000bb",
        security_group_id="sg-000000000000000cc",
        expires_at_value="2026-08-06T06:00:00Z",
        settings=SETTINGS,
        spot=spot,
    )


def joined(argv: tuple[str, ...]) -> str:
    return " ".join(argv)


def test_the_expiry_is_an_absolute_utc_timestamp() -> None:
    """Mutation: emit a duration, or a local time.

    A duration has to be joined to LaunchTime, and LaunchTime is the wrong clock: a stopped and
    restarted instance keeps its original one, so a duration-based janitor would kill a restarted
    machine at once. Absolute also makes an extension one unambiguous write.
    """
    assert expires_at(datetime(2026, 8, 5, 22, 0, 0, tzinfo=UTC), 8) == "2026-08-06T06:00:00Z"


def test_the_launch_carries_both_governance_tags_on_the_instance() -> None:
    """THE STATEMENT THE WHOLE RECLAIM SERVICE RESTS ON.
    Mutation: drop ExpiresAt, or spell either key in lower case.

    The researcher role refuses a launch with no ExpiresAt outright, so dropping it is a launch
    that fails. Spelling either key in lower case is worse: aws:RequestTag is case-sensitive, so
    the role refuses it for a reason whose message names a tag rather than a case, and if it were
    somehow admitted the janitor's filter would skip the machine for ever.
    """
    text = joined(launch())

    assert f"{{Key={PROJECT_TAG_KEY},Value=mixlaw}}" in text
    assert f"{{Key={EXPIRES_AT_TAG_KEY},Value=2026-08-06T06:00:00Z}}" in text
    assert "ResourceType=instance" in text


def test_the_volume_carries_the_project_tag_too() -> None:
    """Mutation: tag the instance only.

    The role's Project condition is scoped to instance/* and volume/*, so an untagged volume is a
    refused launch rather than an untidy one. It is also where a stopped machine's remaining cost
    lives, and an untagged volume is spend no team's total can account for.
    """
    assert "ResourceType=volume" in joined(launch())


def test_the_machine_is_findable_again_by_the_person_and_the_project() -> None:
    """Mutation: tag only the project.

    Two people on one project both get a machine, and a verb that found the other person's would
    put one researcher's session on another's instance. The lane tag carries the person, and
    find_machine_argv filters on both.
    """
    assert f"{{Key={LANE_TAG_KEY},Value=caiiris}}" in joined(launch())

    text = joined(find_machine_argv(project="mixlaw", person="caiiris"))

    assert f"Name=tag:{PROJECT_TAG_KEY},Values=mixlaw" in text
    assert f"Name=tag:{LANE_TAG_KEY},Values=caiiris" in text
    assert "Name=instance-state-name,Values=pending,running" in text


def test_the_lane_tag_is_prefixed_so_the_role_s_tag_deny_does_not_cover_it() -> None:
    """Mutation: name it "Lane", beside Project and ExpiresAt.

    The researcher role denies stripping any key in GOVERNANCE_TAG_KEYS after launch. The lane tag
    is not one of those: it is the platform's own bookkeeping rather than a governance fact, and a
    bare name would put it under a deny it does not belong to. The prefixed spelling is the same
    convention researcher_lane.py's WARNING_TAG_KEY uses, for the same reason.
    """
    assert LANE_TAG_KEY.startswith("edullm:")
    assert LANE_TAG_KEY not in GOVERNANCE_TAG_KEYS


def test_the_machine_carries_the_instance_profile_that_makes_a_session_possible() -> None:
    """Mutation: leave the profile off.

    Session Manager works because the SSM agent can call back, and the agent authenticates with
    the instance profile. Without it the machine boots, runs, bills, and answers no session, and
    the failure reads as "target not connected" with nothing naming the cause.
    """
    assert "--iam-instance-profile" in launch()
    assert f"Name={LANE_INSTANCE_PROFILE}" in joined(launch())


def test_the_root_volume_is_the_size_configuration_names() -> None:
    """Mutation: leave the block device mapping off and take the AMI's default.

    The deep-learning AMI's default root volume is 30 GiB and the image is most of it. The
    account has already paid for that once, on the P-family instances that killed three
    pre-training arms. The size is read from config/reports/working-tier.yaml rather than typed.
    """
    assert f"VolumeSize={SETTINGS.root_volume_gib}" in joined(launch())
    assert "VolumeType=gp3" in joined(launch())


def test_the_metadata_service_is_version_two_only() -> None:
    """Mutation: leave the metadata options at the AMI's default.

    The instance profile's credentials are served by the metadata service, and IMDSv1 hands them
    to anything that can make an HTTP request from the box, which on a machine somebody is
    experimenting on is a wide set. Required, not optional, is the whole of the difference.
    """
    assert "HttpTokens=required" in joined(launch())


def test_on_demand_is_the_default_and_names_no_market_option() -> None:
    """THE DEFAULT THAT KEEPS THE JANITOR'S STOP WORKING.
    Mutation: make spot the default.

    A one-time Spot instance cannot be stopped, so a Spot default hands the reclaim service
    exactly the machines it cannot reclaim. decisions.md carries the argument under "The lane
    runs On-Demand and --spot is the persistent stop form", and records that it inverts a
    sentence system-overview.md used to carry.
    """
    assert "--instance-market-options" not in launch()


def test_spot_is_the_one_form_that_can_still_be_stopped() -> None:
    """Mutation: ask for spot without the two options.

    RunInstances documents persistent Spot requests as supported only when the interruption
    behaviour is hibernate or stop, and stop is what makes StopInstances work on the result. Both
    halves or neither: a one-time request with stop behaviour is refused by EC2, and a persistent
    request with terminate behaviour is refused too.
    """
    text = joined(launch(spot=True))

    assert "MarketType=spot" in text
    assert "SpotInstanceType=persistent" in text
    assert "InstanceInterruptionBehavior=stop" in text


def test_spot_changes_the_market_option_and_nothing_else_about_the_launch() -> None:
    """Mutation: let --spot also drop a tag, or move the volume size.

    --spot is a purchasing decision and the janitor has to be able to reclaim the result exactly
    as it reclaims an On-Demand one. Anything else that moved with it would be a second machine
    shape, tested half as often, and the half that is not tested is the one billing overnight.
    """
    on_demand = launch()
    spot = launch(spot=True)
    market = ("--instance-market-options", spot[spot.index("--instance-market-options") + 1])

    assert tuple(item for item in spot if item not in market) == on_demand


def test_entering_the_lane_declares_a_project_a_lifetime_and_a_person() -> None:
    """Mutation: drop --source-identity.

    The trust policy demands all three with a "?*" presence test, and sts:SetSourceIdentity has
    to be in the caller's request as well as in the trust policy's action list. Dropping any one
    of them fails AssumeRole with a message that names none of them.
    """
    argv = assume_lane_argv(
        account=EXAMPLE_ACCOUNT, project="mixlaw", person="caiiris", lifetime_hours=8
    )
    text = joined(argv)

    assert f"role/{RESEARCHER_ROLE_NAME}" in text
    assert "--source-identity caiiris" in text
    assert "Key=project,Value=mixlaw" in text
    assert "Key=lifetime,Value=8" in text


def test_the_source_identity_is_the_string_the_working_tier_deny_fences_on() -> None:
    """**THE ONE MISMATCH THAT DENIES EVERY WRITE WITH NO EXPLANATION.**
    Mutation: pass the caller ARN, or the session name, as the source identity.

    The researcher role's seventh statement denies writes outside
    edullm-work/*/${aws:SourceIdentity}/*, and working_prefix builds the path from the same
    person. If these two ever disagree the machine starts, the session opens, and every sync
    fails with AccessDenied naming no cause. Asserted as one equality rather than as two facts
    about two functions, because agreement is the property and either alone is not.
    """
    from edullm_platform.cli.lane import person_from_caller_arn, working_prefix

    person = person_from_caller_arn(
        f"arn:aws:sts::{EXAMPLE_ACCOUNT}:assumed-role/Intern-caiiris-sbsandbox"
        "/broker-caiiris-1785873426"
    )
    assert person is not None
    argv = assume_lane_argv(
        account=EXAMPLE_ACCOUNT, project="mixlaw", person=person, lifetime_hours=8
    )
    declared = argv[argv.index("--source-identity") + 1]

    assert working_prefix(team="memory-split", person=person).endswith(f"/{declared}/")


def test_the_assumed_credentials_become_an_environment_and_not_a_file() -> None:
    """Mutation: write a profile into ~/.aws/credentials.

    The done-condition for this slice is that nobody types an AWS profile name, and a verb that
    wrote one would have made a profile the researcher then has to know about. An environment
    passed to one child process disappears when the process does.
    """
    environment = credentials_environment(
        {
            "AccessKeyId": "AKIAEXAMPLE",
            "SecretAccessKey": "secret",
            "SessionToken": "token",
        }
    )

    assert environment == {
        "AWS_ACCESS_KEY_ID": "AKIAEXAMPLE",
        "AWS_SECRET_ACCESS_KEY": "secret",
        "AWS_SESSION_TOKEN": "token",
    }
