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
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
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
from edullm_platform.researcher_lane import (
    EXPIRES_AT_TAG_KEY,
    PROJECT_TAG_KEY,
    RESEARCHER_ROLE_NAME,
)

__all__ = [
    "GPU_AMI_PARAMETER",
    "LANE_INSTANCE_PROFILE",
    "LANE_TAG_KEY",
    "SCRATCH_BUCKET",
    "SESSION_PLUGIN",
    "WORKING_TIER_SETTINGS_PATH",
    "LaneRequest",
    "WorkingTierSettings",
    "agent_online_argv",
    "assume_lane_argv",
    "credentials_environment",
    "expires_at",
    "find_machine_argv",
    "instance_type_for",
    "lane_refusals",
    "load_working_tier_settings",
    "missing_plugin_refusal",
    "notebook_forward_argv",
    "person_from_caller_arn",
    "placement_warning",
    "remote_command_argv",
    "remote_script",
    "run_instances_argv",
    "shell_session_argv",
    "ssh_proxy_command",
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
#: infra/scratch-bucket.yaml is applied by hand. The name is the overview's and a person types it.
SCRATCH_BUCKET: Final = "edullm-scratch"

#: The instance profile the lane machine carries. infra/iam/lane-instance-role.yaml creates it,
#: and the launch passes it as ``--iam-instance-profile Name=<this>``. A rename on either side is
#: a launch that fails after a machine has already been priced.
LANE_INSTANCE_PROFILE: Final = "edullm-lane-instance"

WORKING_TIER_SETTINGS_PATH: Final = "config/reports/working-tier.yaml"

#: Where the lane's image comes from, resolved at launch rather than pinned. The parameter is
#: Amazon's and it moves; on 2026-08-05 it answered ami-0326665395a428ccf, which is the image the
#: one instance in the platform's VPC that Systems Manager reports as Online is running. Reading
#: the parameter is what keeps a lane machine on a current driver without anybody editing a
#: template, and it costs one ssm:GetParameter the boundary does not deny.
GPU_AMI_PARAMETER: Final = (
    "/aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id"
)

#: The tag that says which person's lane a machine belongs to, carrying the source identity.
#:
#: Prefixed, so it stays out of the researcher role's GOVERNANCE_TAG_KEYS and the tag-stripping
#: deny does not cover a key the lane may legitimately rewrite. It is what lets a second
#: `edullm run` find the machine the first one started instead of starting another.
LANE_TAG_KEY: Final = "edullm:lane"

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
    return f"s3://{SCRATCH_BUCKET}/{working_prefix(team=team, person=person)}{segment}/"


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


def expires_at(now: datetime, lifetime_hours: int) -> str:
    """The absolute UTC instant the janitor may stop this machine at, ISO-8601 with a Z.

    Seconds included and sub-seconds not, because the janitor compares this against a sweep that
    runs on a minute boundary. Absolute rather than a duration, for the reason
    ``docs-frank/reference/aws-spend-controls.md`` gives under "The helper" and the researcher
    role's template repeats: LaunchTime is the wrong clock for a duration, and an extension is one
    unambiguous write where a duration has to be read, interpreted and summed.
    """
    return (now + timedelta(hours=lifetime_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def assume_lane_argv(
    *, account: str, project: str, person: str, lifetime_hours: int
) -> tuple[str, ...]:
    """Enter the lane, declaring the three things the trust policy demands.

    **THE VERB DOES THIS ITSELF RATHER THAN ASKING FOR ``tools/enter_researcher_lane.py``'S
    EXPORTS.** The slice's done-condition is a machine in one command with no AWS profile typed,
    and two commands with an ``eval`` between them is not that. The helper is still the right tool
    for somebody who wants a lane shell of their own, and this duplicates its call rather than its
    purpose.

    One hour, which costs nothing: a chained session is capped at an hour whatever is asked for.
    The lifetime tag is what the person promised and is a different number from the session's.
    """
    return (
        "aws",
        "sts",
        "assume-role",
        "--role-arn",
        f"arn:aws:iam::{account}:role/{RESEARCHER_ROLE_NAME}",
        "--role-session-name",
        f"lane-{project}"[:64],
        "--source-identity",
        person,
        "--tags",
        f"Key=project,Value={project}",
        f"Key=lifetime,Value={lifetime_hours}",
        "--duration-seconds",
        "3600",
        "--output",
        "json",
    )


def credentials_environment(assumed: Mapping[str, object]) -> dict[str, str]:
    """An ``sts assume-role`` Credentials block as the three variables every AWS tool reads.

    An environment for one child process rather than a profile in a file, because a profile is a
    thing a researcher then has to know the name of, and the done-condition for this slice is that
    they never type one.
    """
    return {
        "AWS_ACCESS_KEY_ID": str(assumed["AccessKeyId"]),
        "AWS_SECRET_ACCESS_KEY": str(assumed["SecretAccessKey"]),
        "AWS_SESSION_TOKEN": str(assumed["SessionToken"]),
    }


def find_machine_argv(*, project: str, person: str) -> tuple[str, ...]:
    """Whether this person already has a machine for this project, and which one.

    Both tags, because two people on one project both get a machine and finding the other
    person's would put one researcher's session on another's instance. ``pending`` is included
    with ``running`` so a second invocation thirty seconds after the first waits for the machine
    that is coming rather than starting a second one.
    """
    return (
        "aws",
        "ec2",
        "describe-instances",
        "--filters",
        f"Name=tag:{PROJECT_TAG_KEY},Values={project}",
        f"Name=tag:{LANE_TAG_KEY},Values={person}",
        "Name=instance-state-name,Values=pending,running",
        "--query",
        "Reservations[].Instances[].InstanceId",
        "--output",
        "json",
    )


def run_instances_argv(
    *,
    request: LaneRequest,
    instance_type: str,
    image_id: str,
    subnet_id: str,
    security_group_id: str,
    expires_at_value: str,
    settings: WorkingTierSettings,
    spot: bool,
) -> tuple[str, ...]:
    """One lane machine, with everything the role, the janitor and the session need.

    **NO KEY PAIR AND NO PUBLIC PORT.** The connection is Systems Manager, which reaches the
    machine through the agent's own outbound call, so there is nothing to open and no key to
    distribute. The security group this is given has zero ingress rules and that is correct.

    **ON-DEMAND UNLESS ASKED OTHERWISE, WHICH IS THE ONE PLACE THIS PARTS FROM
    ``system-overview.md``.** A one-time Spot instance cannot be stopped, so the plain reading of
    that document hands the expiry janitor a machine it cannot reclaim. ``--spot`` builds the one
    form ``RunInstances`` will make that ``StopInstances`` accepts, and ``decisions.md`` records
    the departure under "The lane runs On-Demand and --spot is the persistent stop form".
    """
    tags = (
        f"ResourceType=instance,Tags=["
        f"{{Key={PROJECT_TAG_KEY},Value={request.project}}},"
        f"{{Key={EXPIRES_AT_TAG_KEY},Value={expires_at_value}}},"
        f"{{Key={LANE_TAG_KEY},Value={request.person}}},"
        f"{{Key=Name,Value=lane-{request.person}-{request.project}}}]"
    )
    volume_tags = f"ResourceType=volume,Tags=[{{Key={PROJECT_TAG_KEY},Value={request.project}}}]"
    market: tuple[str, ...] = ()
    if spot:
        market = (
            "--instance-market-options",
            (
                "MarketType=spot,SpotOptions={"
                "SpotInstanceType=persistent,InstanceInterruptionBehavior=stop}"
            ),
        )
    return (
        "aws",
        "ec2",
        "run-instances",
        "--image-id",
        image_id,
        "--instance-type",
        instance_type,
        "--subnet-id",
        subnet_id,
        "--security-group-ids",
        security_group_id,
        "--iam-instance-profile",
        f"Name={LANE_INSTANCE_PROFILE}",
        "--metadata-options",
        "HttpTokens=required,HttpEndpoint=enabled",
        "--block-device-mappings",
        (
            "DeviceName=/dev/sda1,Ebs={"
            f"VolumeSize={settings.root_volume_gib},VolumeType=gp3,DeleteOnTermination=true"
            "}"
        ),
        "--tag-specifications",
        tags,
        volume_tags,
        *market,
        "--query",
        "Instances[0].InstanceId",
        "--output",
        "text",
    )


#: The one thing that has to be on a researcher's laptop beyond the AWS CLI. It is a separate
#: install and it is not optional: every session below goes through it.
SESSION_PLUGIN: Final = "session-manager-plugin"


def shell_session_argv(instance_id: str) -> tuple[str, ...]:
    """A shell on the machine, with no document named.

    The default is the account's own session preference, which is what somebody asking for a
    shell means. Nothing is opened, nothing is forwarded and no key exists.
    """
    return ("aws", "ssm", "start-session", "--target", instance_id)


def notebook_forward_argv(
    instance_id: str, *, settings: WorkingTierSettings, local_port: int
) -> tuple[str, ...]:
    """A tunnel from a port on the laptop to Jupyter's port on the machine.

    **NOTHING IS OPENED ON THE INSTANCE AND NOTHING IS EXPOSED.** Jupyter binds loopback there and
    the bytes travel through the agent's outbound connection, so the security group keeps its zero
    ingress rules and a notebook is never reachable from the internet. That is the whole reason
    this is a forward rather than a port and a rule.
    """
    return (
        "aws",
        "ssm",
        "start-session",
        "--target",
        instance_id,
        "--document-name",
        "AWS-StartPortForwardingSession",
        "--parameters",
        (f'{{"portNumber":["{settings.notebook_port}"],"localPortNumber":["{local_port}"]}}'),
    )


def remote_command_argv(instance_id: str, *, command: str) -> tuple[str, ...]:
    """One command on the machine, with its output streaming back as it is written.

    ``AWS-StartNonInteractiveCommand`` rather than ``ssm send-command``. SendCommand collects
    output and returns it at the end, truncated at 24,000 characters unless somebody configures a
    bucket for it, and ``edullm run``'s own sentence in the CLI is that it streams.
    """
    return (
        "aws",
        "ssm",
        "start-session",
        "--target",
        instance_id,
        "--document-name",
        "AWS-StartNonInteractiveCommand",
        "--parameters",
        f'{{"command":["{command}"]}}',
    )


def agent_online_argv(instance_id: str) -> tuple[str, ...]:
    """Whether Systems Manager has heard from this machine yet.

    Asked of Systems Manager rather than of EC2, and the difference costs a confusing failure. An
    instance passes its EC2 status checks a minute or two before the agent registers, so an EC2
    reading says ready and the session then fails with "target not connected", which names
    neither the wait nor the cause.
    """
    return (
        "aws",
        "ssm",
        "describe-instance-information",
        "--filters",
        f"Key=InstanceIds,Values={instance_id}",
        "--query",
        "InstanceInformationList[0].PingStatus",
        "--output",
        "text",
    )


def ssh_proxy_command(instance_id: str) -> str:
    """The one line that makes an editor over SSH work, printed rather than installed.

    VS Code Remote-SSH and plain ``ssh`` both drive a ``ProxyCommand``, and this is the documented
    one for Session Manager. It is printed because ``~/.ssh/config`` is a file this binary does not
    own and may not be the only thing writing to.
    """
    return (
        'ProxyCommand sh -c "aws ssm start-session --target '
        f"{instance_id} --document-name AWS-StartSSHSession "
        '--parameters portNumber=%p"'
    )


def missing_plugin_refusal() -> Refusal:
    """What to say when the laptop has the AWS CLI and not the piece that carries a session."""
    return Refusal(
        code="session_plugin_missing",
        detail=(
            f"{SESSION_PLUGIN} is not on PATH. A lane session is a Systems Manager session rather "
            "than SSH, which is what means there is no key to hold and no port open on the "
            "machine, and the plugin is the piece of that which runs on your laptop. Install it "
            "from the AWS documentation for the Session Manager plugin, then run this again. "
            "Nothing was started and nothing is billing."
        ),
    )


def remote_script(*, uri: str, project: str, command: str) -> str:
    """What runs on the machine for one ``edullm run``, as one line of shell.

    Three acts and the middle one is the researcher's. Sync the tree down, run what was asked,
    sync back whatever it wrote. The status is captured between the second and the third, so a
    command that failed still gets its output carried up, and it is printed last on a line the
    verb parses, because ``start-session`` exits with the plugin's status rather than the remote
    command's.
    """
    directory = f"/work/{project}"
    return (
        f"set -u; mkdir -p {directory}; "
        f"aws s3 sync {uri} {directory} --only-show-errors; "
        f"cd {directory}; "
        f"({command}); status=$?; "
        f"aws s3 sync {directory} {uri} --only-show-errors; "
        f'echo "edullm-exit:$status"'
    )
