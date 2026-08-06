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

import json
import random
import re
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Final, Literal

from pydantic import Field

from edullm_platform.cli.configuration import (
    ConfigurationUnreadableError,
    ReviewedConfiguration,
)
from edullm_platform.cli.preflight import Refusal
from edullm_platform.cli.workspace import WINDOWS
from edullm_platform.contracts.base import ContractModel, serialize_decimal
from edullm_platform.contracts.workload import ComputeProfile
from edullm_platform.placement import (
    CAPACITY_FILENAME,
    PLACES_AFTER_A_WAIT,
    PLACES_RELIABLY,
    PlacementRecord,
    UnreadableCapacityError,
    read_capacity,
)
from edullm_platform.precision import gpu_of
from edullm_platform.researcher_lane import (
    EXPIRES_AT_TAG_KEY,
    PROJECT_TAG_KEY,
    RESEARCHER_ROLE_NAME,
)
from edullm_platform.reviewed_configuration import ConfigFile, load_config_file

__all__ = [
    "ARM_MACHINES",
    "AWS_LOGIN_COMMAND",
    "GPU_AMI_PARAMETER",
    "LANE_INSTANCE_PROFILE",
    "LANE_TAG_KEY",
    "MACOS",
    "PLATFORM_NETWORK_NAME",
    "PLUGIN_DOWNLOADS",
    "SCRATCH_BUCKET",
    "SESSION_PLUGIN",
    "STOPPABLE_STATES",
    "ZONE_SHAPED_REFUSALS",
    "DefaultedCompute",
    "LaneExpiry",
    "LaneMachine",
    "LaneRequest",
    "LaneSubnet",
    "RanFor",
    "WorkingTierSettings",
    "ZoneAttempt",
    "agent_online_argv",
    "another_zone_may_answer",
    "assume_lane_argv",
    "command_line",
    "credentials_environment",
    "default_compute_profile",
    "expires_at",
    "expiry_for_a_new_machine",
    "find_lane_machines_argv",
    "find_machine_argv",
    "find_subnets_argv",
    "instance_type_for",
    "lane_machines",
    "lane_refusals",
    "lane_subnets",
    "load_working_tier_settings",
    "machine_already_running",
    "machine_for_project",
    "missing_plugin_refusal",
    "no_machine_to_stop",
    "no_zone_had_this_shape",
    "notebook_forward_argv",
    "person_from_caller_arn",
    "placement_said",
    "placement_verdict",
    "placement_warning",
    "plugin_install_commands",
    "priced_as",
    "ran_for",
    "ran_for_said",
    "refusal_code",
    "remote_command_argv",
    "remote_script",
    "run_instances_argv",
    "shell_session_argv",
    "ssh_proxy_command",
    "subnets_to_try",
    "terminate_argv",
    "under_a_shell",
    "what_stopping_did",
    "whose_machine_refusals",
    "working_prefix",
    "working_uri",
    "zones_offering",
    "zones_offering_argv",
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

#: Where the lane's image comes from, resolved at launch rather than pinned. The parameter is
#: Amazon's and it moves; on 2026-08-05 it answered ami-0326665395a428ccf, which is the image the
#: one instance in the platform's VPC that Systems Manager reports as Online is running. Reading
#: the parameter is what keeps a lane machine on a current driver without anybody editing a
#: template, and it costs one ssm:GetParameter the boundary does not deny.
GPU_AMI_PARAMETER: Final = (
    "/aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id"
)

#: The Name tag prefix and the group name ``infra/batch-network.yaml`` gives the platform's VPC.
#: Read rather than pinned, so a redeploy that moves an id moves the lane with it.
#:
#: HERE RATHER THAN IN ``main.py``, WHICH IS WHERE IT WAS. This module's header says it owns the
#: exact argv of every command the two verbs run, and :func:`find_subnets_argv` is now one of
#: them, so the name and the filter that interpolates it belong in one file. ``main`` imports it
#: for the security group query, which is the one call still assembled there.
PLATFORM_NETWORK_NAME: Final = "sbsandbox-intern-edullm-batch"

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


def load_working_tier_settings(directory: Path | None = None) -> WorkingTierSettings:
    """The four numbers a machine is launched with, from a directory somebody resolved.

    **THIS IS THE FUNCTION THE TWO VERBS SHIPPED BROKEN ON.** It defaulted to
    ``"config/reports/working-tier.yaml"``, which is a path against the working directory, so
    ``edullm run`` and ``edullm shell`` raised ``FileNotFoundError`` for everybody outside a
    platform checkout -- which is every person either verb was written for. The file was in
    the wheel the whole time, at ``edullm_platform/_config/reports/working-tier.yaml``.
    ``edullm_platform.reviewed_configuration`` carries the rule that replaced it, and the
    three things that now hold it.
    """
    return load_config_file(ConfigFile.WORKING_TIER, WorkingTierSettings, directory=directory)


def working_prefix(*, person: str) -> str:
    """Where one person's working set lives, as an S3 prefix ending in a separator.

    **THE PERSON SEGMENT IS NOT ACCESS CONTROL AND THAT IS WORTH READING BEFORE ARGUING WITH
    IT.** The researcher role's seventh statement fences on ``${aws:SourceIdentity}``, which is
    self-asserted: ``docs-frank/reference/aws-spend-controls.md``, "What the lane does not
    cover", records that nothing stops somebody passing another person's. What the segment buys
    is that two people on one project sync into two prefixes rather than one. Sharing a prefix,
    each ``aws s3 sync`` deletes what the other wrote and neither is told. That is a collision
    and not a breach, and somebody who takes it for a security boundary will eventually notice
    it is a weak one and remove it.

    NO TEAM SEGMENT ABOVE IT, WHICH THERE WAS UNTIL 2026-08-05. The fence never enforced one, so
    it was a label; seven people sit on two groups so a lane would have had to resolve a team to
    know where to sync; and ``config/organization.yaml`` defines this tier as the work costed to
    nobody, which is the one dimension a team segment would organise it by.
    ``docs-frank/reference/decisions.md`` carries the ruling and says why the outputs bucket
    keeps its own team segment rather than being tidied to match.

    The trailing separator is what makes this a directory to every tool that lists one, and
    without it a prefix search for one person also finds everybody whose name starts the same
    way. It is the same character the role's excepted path carries for the same reason.
    """
    return f"{_UNSAFE_IN_A_SEGMENT.sub('-', person)}/"


def working_uri(*, person: str, project: str) -> str:
    """The s3:// address one project's working set is synced to and from."""
    segment = _UNSAFE_IN_A_SEGMENT.sub("-", project)
    return f"s3://{SCRATCH_BUCKET}/{working_prefix(person=person)}{segment}/"


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

    Three fields, against the fifteen a submission carries. That difference is the slice: a
    submission is a record and needs everything a record names, and a lane ask is a machine and a
    place to put files.

    NO TEAM, AND ITS ABSENCE IS THE FIELD WORTH A SENTENCE. There was one until 2026-08-05,
    carried solely because the working tier was laid out ``<team>/<person>/``. It is
    ``<person>/`` now, nothing else on this route reads a group, and a lane that resolved one
    anyway would make ``edullm run`` refuse ``team_is_ambiguous`` to decide a segment that no
    longer exists.
    """

    project: str
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


def placement_verdict(
    configuration: ReviewedConfiguration, profile_name: str
) -> PlacementRecord | None:
    """What ``config/capacity.yaml`` records about this shape, or nothing where it has no entry.

    **THE FILE IS READ HERE RATHER THAN OFF ``ReviewedConfiguration``, AND THAT IS TO KEEP A
    CORRECTION THIS REPOSITORY ALREADY PAID FOR.** ``load_reviewed_configuration`` opens six
    files, ``tests/test_release_tag_workflow.py`` derives the release trigger by watching it open
    them, and ``release-tag.yml``'s own header records what happened when capacity counted as
    reviewed configuration: ``v0.2.1`` announced that ``edullm check`` would answer differently
    when the only file that had moved was ``config/capacity.yaml``, and everybody who re-installed
    on that sentence did it for nothing. A seventh field here would reinstate that, so the reader
    and the verdicts are shared with the compile step and the loader is left alone.

    **THE RECORD IS HANDED BACK AND NOT ONLY THE SENTENCE, WHICH IS WHAT ``--json`` NEEDS.** A
    caller that had to recognise a verdict by matching the prose would be reading a string this
    repository rewords, which is the thing ``AGENTS.md`` tells every agent not to do.
    """
    return next(
        (record for record in _capacity(configuration) if record.profile == profile_name), None
    )


def _capacity(configuration: ReviewedConfiguration) -> tuple[PlacementRecord, ...]:
    """Every recorded verdict, out of one read of the file.

    Shared by the lookup above and by :func:`default_compute_profile`, which weighs every
    profile at once and would otherwise open the file once per candidate.
    """
    path = configuration.directory / CAPACITY_FILENAME
    try:
        return tuple(read_capacity(path))
    except UnreadableCapacityError as exc:
        # Re-raised as the class ``main`` already turns into exit 2. ``read_capacity`` is right to
        # raise rather than default to "everything places", and a ValueError escaping a verb is a
        # traceback in front of a researcher, which is the one thing this binary promises not to
        # do. An unreadable capacity file is an unusable installation and reads as one.
        raise ConfigurationUnreadableError(
            f"{path} is not a document edullm can act on, so whether a machine is likely to "
            f"start cannot be said: {exc}"
        ) from exc


def placement_warning(configuration: ReviewedConfiguration, profile_name: str) -> str | None:
    """The lane's line about finding this shape, or nothing where the file is quiet about it.

    A sentence and never a refusal, which is the same choice ``system-overview.md`` records the
    compile step making under "The machines". The reasoning there transfers exactly: the person
    asking is the person paying and the wait is theirs to accept.

    ``edullm_platform.placement`` owns the verdicts, so there is no second table and no second
    parse. What is not shared is the rendering: that module's sentences are markdown for a pull
    request comment, and this one is a line in somebody's terminal.

    **THE LAST CLAUSE IS THIS VERB'S AND IS THE REASON THE FACT IS COMPOSED SEPARATELY.**
    ``run`` and ``shell`` are about to sit in a loop waiting for a machine, so what to do about
    the warning is Ctrl-C. ``check`` starts nothing and has nothing to interrupt, and printing
    that clause there would be an instruction about a wait the verb does not impose.
    """
    said = placement_said(placement_verdict(configuration, profile_name))
    return None if said is None else f"{said} Ctrl-C stops waiting and starts nothing."


def placement_said(verdict: PlacementRecord | None) -> str | None:
    """One recorded verdict as the fact a terminal shows, or nothing where there is none.

    No next step in it, because the two callers have different ones. See
    :func:`placement_warning` for the lane's.

    Split from the read so that a caller wanting both the verdict and the sentence -- which
    ``check`` does, one for ``--json`` and one for the paragraphs -- opens the file once. Two
    reads could not disagree, both going through the same reader, but they would describe two
    moments and there is no reason to have two.

    The wording here is provisional: it carries the verdict, the instrument that reached it and
    the file, which are the three facts, and somebody rewriting these strings should keep all
    three.
    """
    if verdict is None or verdict.places == PLACES_RELIABLY:
        return None
    if verdict.places == PLACES_AFTER_A_WAIT:
        # ``read_capacity`` refuses this verdict without a wait, so there is always one to quote.
        assert verdict.wait is not None
        return (
            f"config/{CAPACITY_FILENAME} records {verdict.profile} as arriving after a wait, "
            f"measured by a {verdict.measured_by}. {verdict.wait}"
        )
    return (
        f"config/{CAPACITY_FILENAME} records {verdict.profile} as placing "
        f"{verdict.places}, measured by a {verdict.measured_by}. A machine may take a while to "
        "arrive, or never arrive, and nothing here refuses it."
    )


@dataclass(frozen=True)
class DefaultedCompute:
    """The shape the lane starts when nobody names one, and the line that says what it did.

    **THE NAME AND THE SENTENCE ARE ONE OBJECT FOR THE REASON :class:`LaneExpiry` IS.** The
    line quotes a rate and a reason, and a line composed anywhere but here would be quoting a
    choice it did not make -- which is how ``edullm run`` came to print an expiry the machine
    did not have.
    """

    #: The compute profile's name, exactly as ``config/workload-catalog.yaml`` spells it.
    profile: str
    #: What the researcher is told was chosen for them, and how to choose otherwise.
    said: str


def default_compute_profile(configuration: ReviewedConfiguration) -> DefaultedCompute | None:
    """The machine to start when the researcher named none, or nothing where there is none.

    **``--project`` IS STILL REQUIRED AND THIS IS NOT THE THIN END OF DEFAULTING IT.** The two
    flags are different in kind and the difference decides the answer. A project is a name only
    the person has: it tags the instance and the volume, it is the last segment of the working
    prefix, and a wrong one puts two unrelated pieces of work under one bill in a prefix nobody
    chose -- and nothing afterwards can tell them apart. A compute profile is a price, it is
    declared in reviewed configuration, and it is visible in the first line of ``nvidia-smi``.
    ``lane_refusals`` refuses a misspelled *destination* and an unnameable caller; a shape is
    neither, so nothing in this module's own rule covers it.

    **CHEAPEST THAT CAN RUN THE THING, WHICH IS THE SCAFFOLD'S RULE AND NOT ITS FUNCTION.**
    ``cli/scaffold.py`` keys its choice on a workload profile, and the lane has no workload,
    no repository and no catalog entry to read one from. What transfers is the ordering, and
    the reason it exists: ``gpu-1xt4`` is the cheapest GPU shape here and its T4 is Turing,
    which has no bfloat16 in the hardware, so the shape that looks cheapest is the one a
    trainer dies on after the machine is billed. ``precision.gpu_of`` keys on the instance
    family the catalog already declares, so a shape priced, renamed or demoted there is
    answered here without an edit.

    **THREE FILTERS, EACH OF WHICH FALLS THROUGH RATHER THAN EMPTYING THE LIST.** A default
    that refused would be worse than the required flag it replaced, so a catalog in which
    nothing has bfloat16, or nothing places, still yields the cheapest GPU shape and says which
    of the two it could not honour. ``provisioned`` is deliberately not one of the filters, for
    the reason :func:`instance_type_for` gives: it means a Batch queue exists, and a lane
    machine is not a Batch job.

    Nothing here refuses anything, which is what keeps ``tests/test_lane_verdicts.py``'s ruling
    intact. ``gpu_of`` answers what a card can do and is not consulted about whether a person
    may have one.
    """
    gpus = [
        profile
        for profile in configuration.catalog.compute_profiles
        if profile.accelerator == "gpu"
    ]
    if not gpus:
        return None
    capable = [profile for profile in gpus if _has_bfloat16(profile)]
    placing = [
        profile
        for profile in (capable or gpus)
        if placement_said(
            next(
                (record for record in _capacity(configuration) if record.profile == profile.name),
                None,
            )
        )
        is None
    ]
    chosen = min(
        placing or capable or gpus, key=lambda profile: (profile.hourly_rate_usd, profile.name)
    )
    return DefaultedCompute(
        profile=chosen.name, said=_defaulted_compute_said(chosen, capable, placing)
    )


def _has_bfloat16(profile: ComputeProfile) -> bool:
    card = gpu_of(profile)
    return card is not None and card.architecture.supports_bfloat16


def _defaulted_compute_said(
    chosen: ComputeProfile,
    capable: Sequence[ComputeProfile],
    placing: Sequence[ComputeProfile],
) -> str:
    """Why this shape and not a cheaper one, built from the filters that actually survived.

    **THE REASON IS COMPOSED FROM WHAT HAPPENED RATHER THAN WRITTEN OUT ONCE**, because both
    filters above fall through. A sentence claiming bfloat16 over a catalog where nothing has
    it would be the same class of untruth as an expiry that is not the tag: correct on the day
    it was typed and false the first time the list it describes changes underneath it.
    """
    because = [
        *(["whose card has bfloat16"] if capable else []),
        *([f"that config/{CAPACITY_FILENAME} records as placing"] if placing else []),
    ]
    reason = f" {' and '.join(because)}" if because else ""
    return (
        f"No --compute given, so this starts {chosen.name}: {chosen.instance_type} at "
        f"${serialize_decimal(chosen.hourly_rate_usd)}/hour, the cheapest GPU shape in "
        f"config/workload-catalog.yaml{reason}. Pass --compute to start a different one."
    )


def whose_machine_refusals(*, person: str, project: str) -> tuple[Refusal, ...]:
    """The two refusals every lane verb makes, which are about whose machine and which one.

    **SHARED RATHER THAN RESTATED, BECAUSE ``stop`` MAKES EXACTLY THESE TWO AND NO OTHERS.**
    It resolves no shape -- it acts on a machine that already exists, whatever the catalog now
    says about that machine's type -- so :func:`lane_refusals`' third refusal is not one it can
    make. Two copies of a detail string is how the copy nobody reviewed ends up in front of
    somebody, and both of these name a file and a consequence at length.
    """
    refusals: list[Refusal] = []
    if not person:
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
    if not project:
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
    return tuple(refusals)


def lane_refusals(
    request: LaneRequest, *, configuration: ReviewedConfiguration
) -> tuple[Refusal, ...]:
    """Everything the lane refuses, which is three things and is the whole list.

    **NOTHING HERE IS A PERMISSION AND THAT IS THE TEST EVERY CANDIDATE HAS TO PASS.** Two of
    the three say a destination is misspelled, and the third says the caller cannot be named. Add
    a fourth only if the same is true of it, and read
    ``docs-frank/superpowers/specs/2026-08-04-platform-buildout-design.md`` under "The exploration
    route is a slice, not a non-goal" first.

    IT WAS FOUR UNTIL 2026-08-05 AND THE ONE THAT LEFT IS THE ONE TO NOT PUT BACK.
    ``unknown_team`` checked the spelling of a segment the working tier no longer has. With the
    tier laid out ``<person>/<project>/`` there is no group in the path, so a group named here
    would be withheld rather than misdirected, and that is a permission.

    ``Refusal`` is imported from ``cli/preflight.py`` and no rule there is called. A frozen
    dataclass of two strings is a shape rather than a judgement, and a second refusal type would
    give the CLI two things to render. ``tests/test_lane_verdicts.py`` is where every refusal the
    submission path makes is ruled on one at a time, and it fails when a new one is added there
    without a ruling here.
    """
    refusals = list(whose_machine_refusals(person=request.person, project=request.project))
    if instance_type_for(configuration, request.compute_profile) is None:
        refusals.append(
            Refusal(code="unknown_machine", detail=_no_machine_detail(configuration, request))
        )
    return tuple(refusals)


def _no_machine_detail(configuration: ReviewedConfiguration, request: LaneRequest) -> str:
    """Two causes, two sentences, which is the rule the five defects of 2026-08-06 bought.

    A shape that was named and is not in the catalog is a misspelling, and the remedy is the
    list. No shape at all reaching here means ``--compute`` was omitted *and*
    :func:`default_compute_profile` had nothing to offer, which is a catalog with no GPU
    profile in it -- a broken installation rather than a typing mistake, and a message quoting
    an empty name back at somebody who typed no name would send them looking for the typo.
    """
    offered = ", ".join(sorted(profile.name for profile in configuration.catalog.compute_profiles))
    if not request.compute_profile:
        return (
            "no --compute was given and there was nothing to default to, because "
            "config/workload-catalog.yaml declares no GPU profile for this lane to pick from. "
            f"Name a shape with --compute. Priced here: {offered or 'nothing at all'}."
        )
    return (
        f"{request.compute_profile!r} is not in config/workload-catalog.yaml, so "
        f"there is no instance type to start. Offered: {offered}. Unlike a "
        "submission, an unprovisioned profile is fine here: provisioned means a "
        "Batch queue exists and this is not a Batch job."
    )


def expires_at(now: datetime, lifetime_hours: int) -> str:
    """The absolute UTC instant the janitor may stop this machine at, ISO-8601 with a Z.

    Seconds included and sub-seconds not, because the janitor compares this against a sweep that
    runs on a minute boundary. Absolute rather than a duration, for the reason
    ``docs-frank/reference/aws-spend-controls.md`` gives under "The helper" and the researcher
    role's template repeats: LaunchTime is the wrong clock for a duration, and an extension is one
    unambiguous write where a duration has to be read, interpreted and summed.
    """
    return (now + timedelta(hours=lifetime_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class LaneExpiry:
    """When the janitor may stop this machine, and the one sentence that says so.

    **THE VALUE AND THE SENTENCE ARE ONE OBJECT BECAUSE THEY WERE TWO AND THEY DISAGREED.**
    The verb computed a fresh expiry on every invocation and printed it, while the
    ``ExpiresAt`` tag kept whatever it was handed at launch. On a machine that already
    existed those are different instants, the later one was the one printed, and the work
    stopped while the researcher believed they had time left. The janitor was not wrong: it
    warns at the lead ``config/reports/researcher-lane.yaml`` declares and stops shortly
    after the tag's time, with CloudTrail naming the janitor rather than a person, which is
    it doing exactly what the tag says. The tag was stale.

    So there is one string now, and :meth:`said` is built out of it rather than beside it.
    ``tests/test_lane_expiry.py`` reads the tag out of a launch and the timestamps out of
    the line and asserts there is one instant between them, which is the property; either
    checked alone would have passed throughout the defect.

    **THE TAG IS NOT REWRITTEN ON REUSE, AND THAT IS A DECISION RATHER THAN THE CHEAPER
    HALF.** The other repair was to make the printed time true by writing it onto the
    machine, and ``infra/iam/researcher-role.yaml`` is where that stops being an API call
    and starts being a policy change: ``DenyStrippingGovernanceTagsAfterLaunch`` denies
    ``ec2:CreateTags`` on ``ExpiresAt`` for everything except the launch itself. That
    statement is what stops somebody in the lane removing their own expiry, and the template
    records beside it that IAM cannot compare a tag value against the clock -- so a grant to
    rewrite the tag is a grant to write *any* value into it, a year out included. The
    expiry would stop being a bound in the same edit. It would stop being one by composition
    too: ``--hours`` is held under the bound the profile declares, and a reuse that re-arms
    the clock is that bound taken again on each invocation, which is no bound at all.

    What a person actually expects from running the verb twice is the machine they already
    have, which is what reuse is: a find rather than a launch, and a find reports what it
    found. The surprise the honest reading costs is answered where it lands, in the line
    itself, which says the expiry was set when the machine started and that this command did
    not move it.
    """

    #: Exactly what the machine's ``ExpiresAt`` tag holds, empty only where it holds nothing.
    value: str
    #: Whether this was read off a machine already running, rather than computed for a launch.
    found_running: bool

    def said(self, machine: str) -> str:
        """The line the researcher reads, which cannot quote an instant that is not above.

        Three causes and three sentences, on the rule the lane's five defects bought: every
        one of them printed that the session had ended without saying what had happened, and
        a message two causes share is a message doing none of its job. Here the causes are a
        machine being started, a machine being found, and a machine carrying no expiry at
        all -- and the third is the one a shared sentence would hide, because a machine
        nothing will reclaim would otherwise announce itself by printing the word
        ``expires`` with nothing after it.
        """
        if not self.value:
            return (
                f"{machine} is already running and carries no {EXPIRES_AT_TAG_KEY} tag, so the "
                "expiry janitor cannot see it and nothing will stop it. It is billing until "
                "somebody terminates it."
            )
        if self.found_running:
            return (
                f"{machine} is already running and expires {self.value}, which is the expiry "
                "it was started with. This found that machine rather than starting one, and "
                "did not move its expiry."
            )
        return f"{machine} expires {self.value}"


def expiry_for_a_new_machine(now: datetime, lifetime_hours: int) -> LaneExpiry:
    """The expiry a launch is about to be tagged with, and the line that announces it.

    The same string reaches ``run_instances_argv`` and :meth:`LaneExpiry.said`, so the
    machine and the sentence cannot part company on the one path where they used to agree
    by coincidence rather than by construction.
    """
    return LaneExpiry(value=expires_at(now, lifetime_hours), found_running=False)


def machine_already_running(described: str) -> tuple[str, LaneExpiry] | None:
    """The machine this person already has for this project, with the expiry it carries.

    ``None`` where they have none, which is the ordinary first invocation.

    **THE EXPIRY IS READ OFF THE MACHINE AND NEVER COMPUTED HERE, WHICH IS THE CORRECTION.**
    A second ``edullm run`` finds an instance the first one started and the janitor holds it
    to the tag that launch wrote. Computing one against this invocation's clock produces a
    number that is true of nothing.

    **THE TAG IS MATCHED IN PYTHON AGAINST ``EXPIRES_AT_TAG_KEY`` RATHER THAN IN THE
    ``--query``.** A JMESPath filter would spell the key a second time, in a string no import
    reaches, and the whole point of :mod:`edullm_platform.researcher_lane` owning that
    spelling is that the role's condition, the janitor's reader and the launch cannot say
    three different things. It is also case-sensitive on the AWS side and silently returns
    null on a near miss, so the spelling that drifted would present as a machine with no
    expiry rather than as an error.

    **AN ENTRY THAT IS NOT AN OBJECT IS SKIPPED RATHER THAN UNPACKED.** The shape is
    :func:`find_machine_argv`'s ``--query`` and the two ship in this module together, so the
    only way to see a bare string here is an install whose halves disagree -- and a
    ``AttributeError`` traceback in front of a researcher is the one thing this binary promises
    not to do. Skipping reads as "no machine", which starts a second one, so the finder's own
    test pins the query rather than trusting this guard to notice.
    """
    for entry in json.loads(described.strip() or "[]"):
        if not isinstance(entry, dict):
            continue
        machine = str(entry.get("machine") or "")
        if not machine:
            continue
        tags = entry.get("tags") or []
        found = next(
            (
                tag.get("Value")
                for tag in tags
                if isinstance(tag, dict) and tag.get("Key") == EXPIRES_AT_TAG_KEY
            ),
            "",
        )
        return machine, LaneExpiry(value=str(found or ""), found_running=True)
    return None


#: Every state ``stop`` can find a machine in and has something to say about.
#:
#: **WIDER THAN :func:`find_machine_argv`'S TWO, AND THE EXTRA TWO ARE THE POINT.** ``pending``
#: and ``running`` are what the other verbs look for, because those are the states a session can
#: be opened on. A machine the janitor has already stopped is in neither, and it is invisible to
#: every other verb in this binary while its two hundred gibibytes keep billing: ``run`` will not
#: find it and starts a second machine, and ``expiry_janitor`` decides ``already_stopped`` and
#: leaves it for ever. So the only route out of that state is a verb that can see it, and this
#: is that verb.
STOPPABLE_STATES: Final[tuple[str, ...]] = ("pending", "running", "stopping", "stopped")


@dataclass(frozen=True)
class LaneMachine:
    """One machine in a person's lane, with everything ``stop`` reports about it.

    The project is carried out of the tag rather than passed in beside it, which is what lets
    one call answer both "is there one for this project" and "which projects do you have
    machines for" -- and the second of those is what a mistyped ``--project`` needs.
    """

    machine: str
    project: str
    state: str
    instance_type: str
    #: When EC2 says it started, or nothing where that could not be read. ``None`` rather than a
    #: guess, because this is the only input to what the machine cost and a guessed clock
    #: produces a figure that looks measured.
    launched: datetime | None
    #: When EC2 says it stopped running, or nothing where it is still running or where the
    #: reason it gave carries no instant. With :attr:`launched` this is the interval that was
    #: billed; without it the clock is, and the clock is not the same number.
    stopped: datetime | None
    #: Whether it was bought on Spot, which bills under the catalog's rate.
    spot: bool


def find_lane_machines_argv(*, person: str) -> tuple[str, ...]:
    """Every machine in this person's lane, whatever project it is for and whatever state.

    **THE PERSON TAG IS THE WHOLE FENCE AND IT IS THE ONLY FILTER THAT MATTERS HERE.** The value
    comes from :func:`person_from_caller_arn` reading the caller's own ARN, so a person asking
    this question can only ever ask it about themselves, and somebody else's machine is not in
    the answer to be acted on. ``infra/iam/researcher-role.yaml`` does not fence this -- its
    ``AllowResearchWorkingSet`` statement is ``"*"`` on ``"*"`` and every deny in the policy
    names ``ec2:RunInstances``, ``ec2:CreateTags`` or a bucket -- so a lane credential can
    terminate anything in the account and the refusal has to be built here rather than found
    there. That is why no verb above this takes an instance id: an id typed on a command line
    is an id this filter never saw, and it would be the one way through.

    **THE PROJECT IS NOT FILTERED ON, WHICH IS DELIBERATE AND COSTS NOTHING.** One call answers
    both questions a person asking to stop something can have -- the machine for this project,
    and, when there is none, which projects they do have machines for -- and a mistyped
    ``--project`` that got back a bare "nothing found" would read as "nothing is billing", which
    is the wrong answer to give somebody who is asking precisely because something is.

    ``StateTransitionReason`` is asked for beside the launch time because the two together are
    the interval EC2 billed, and one of them alone is not. It reads ``User initiated (2026-08-06
    13:30:50 GMT)`` on a machine the janitor stopped and is empty on one still running, so the
    field that says *when* a machine stopped arrives in the answer this call already makes --
    no second call, no CloudTrail, and no second of anybody's wait. :func:`ran_for` is what
    spends it.
    """
    return (
        "aws",
        "ec2",
        "describe-instances",
        "--filters",
        f"Name=tag:{LANE_TAG_KEY},Values={person}",
        f"Name=instance-state-name,Values={','.join(STOPPABLE_STATES)}",
        "--query",
        (
            "Reservations[].Instances[].{machine:InstanceId,state:State.Name,"
            "type:InstanceType,launched:LaunchTime,transition:StateTransitionReason,"
            "lifecycle:InstanceLifecycle,tags:Tags}"
        ),
        "--output",
        "json",
    )


def _launched_at(value: object) -> datetime | None:
    """``LaunchTime`` as an instant, or nothing where it is not one.

    The same rule :mod:`edullm_platform.expiry_janitor` follows for ``ExpiresAt`` and for the
    same reason: a value that cannot be read is a machine whose cost cannot be stated, and a
    ``TypeError`` comparing a naive instant against an aware one would be a traceback in front
    of somebody trying to stop a machine that is billing.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


#: The instant inside ``StateTransitionReason``, which is prose with a clock in the middle of it.
#:
#: ``User initiated (2026-08-06 14:13:58 GMT)`` is what a stopped machine carries -- read off
#: ``i-0303e11fbe92f4d9e`` in the sandbox account, which also showed that EC2 writes it the moment
#: the stop is asked for rather than when it completes, so a machine still ``stopping`` already
#: has it. GMT is the only zone EC2 writes here. Anchored on the parenthesis rather than searched
#: for loosely, so that a reason with no instant in it -- ``Client.InstanceInitiatedShutdown``,
#: which a ``shutdown -h`` from inside the machine leaves -- matches nothing and is answered as
#: unknown rather than as a date somebody's prose happened to contain.
_TRANSITION_INSTANT: Final[re.Pattern[str]] = re.compile(
    r"\((\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) GMT\)"
)


def _stopped_at(value: object, *, state: str) -> datetime | None:
    """When EC2 stopped this machine, or nothing where it did not or would not say.

    **THE STATE IS CHECKED AND NOT ONLY THE STRING, WHICH IS THE GUARD AGAINST A STALE REASON.**
    ``StateTransitionReason`` describes the last transition EC2 recorded, and this reads it as
    "the running interval ended here" -- a reading that is only true while the machine is not
    running. Anything still running answers ``None`` without looking at the string, so no
    transition from before a start can be mistaken for the end of the interval it precedes.

    Unparseable is ``None`` rather than a guess, on :func:`_launched_at`'s rule and for its
    reason: what cannot be read is said to be unknown, and the sentence that quotes it says so.
    """
    if state in ("pending", "running") or not isinstance(value, str):
        return None
    found = _TRANSITION_INSTANT.search(value)
    if found is None:
        return None
    return datetime.fromisoformat(found.group(1)).replace(tzinfo=UTC)


def lane_machines(described: str) -> tuple[LaneMachine, ...]:
    """:func:`find_lane_machines_argv`'s answer as the machines it is, skipping anything odd.

    An entry that is not an object, or that carries no instance id, is skipped rather than
    unpacked -- the rule :func:`machine_already_running` and :func:`lane_subnets` already hold,
    for the reason their own notes give. Skipping reads as one fewer machine, which is a verb
    that says it found nothing rather than a verb that raises, and nothing here invents one.

    A machine with no ``Project`` tag keeps an empty project rather than being dropped. The
    launch cannot produce one -- ``RequireProjectTagMatchingTheSessionTag`` denies a
    ``RunInstances`` that omits it -- so this is a machine from before the tag or one somebody
    edited, and it is still a machine of theirs that is billing. Dropping it would hide the one
    kind of machine nothing else can find.
    """
    found: list[LaneMachine] = []
    for entry in json.loads(described.strip() or "[]"):
        if not isinstance(entry, dict):
            continue
        machine = str(entry.get("machine") or "")
        if not machine:
            continue
        tags = entry.get("tags") or []
        project = next(
            (
                str(tag.get("Value") or "")
                for tag in tags
                if isinstance(tag, dict) and tag.get("Key") == PROJECT_TAG_KEY
            ),
            "",
        )
        state = str(entry.get("state") or "")
        found.append(
            LaneMachine(
                machine=machine,
                project=project,
                state=state,
                instance_type=str(entry.get("type") or ""),
                launched=_launched_at(entry.get("launched")),
                stopped=_stopped_at(entry.get("transition"), state=state),
                # EC2 omits InstanceLifecycle entirely for On-Demand rather than saying so, so
                # absent is the ordinary case and only the word "spot" means Spot.
                spot=str(entry.get("lifecycle") or "") == "spot",
            )
        )
    return tuple(found)


def machine_for_project(machines: Sequence[LaneMachine], *, project: str) -> LaneMachine | None:
    """The one machine this person has for this project, or nothing where they have none.

    The first, where a person somehow has two. The lane starts one per person and project and
    :func:`find_machine_argv` reuses it, so a second is a machine started while the first was
    still ``pending`` -- and stopping one of them is strictly better than refusing to stop
    either, which is what an ambiguity refusal here would amount to. Running the verb again
    reaches the second.
    """
    return next((one for one in machines if one.project == project), None)


def no_machine_to_stop(machines: Sequence[LaneMachine], *, project: str) -> str:
    """What to say to somebody whose ``--project`` matched nothing they have.

    **IT NAMES THE PROJECTS THEY DO HAVE MACHINES FOR, WHICH IS THE WHOLE REASON THE FINDER
    DOES NOT FILTER ON ONE.** A person types this verb because they believe something is
    billing. Answering a mistyped project with "nothing found" tells them the opposite of the
    truth in the exact words that sound like reassurance, and they stop looking. Listing what
    is actually running turns a wrong answer into the right one at no extra call.
    """
    others = sorted({one.project or "(no project tag)" for one in machines})
    if not others:
        return (
            f"you have no machine in the lane, for {project!r} or for anything else, so there "
            "is nothing to stop and nothing of yours is billing. This looked for machines "
            "tagged with your own name and no other person's, which is the only kind this can "
            "reach."
        )
    return (
        f"you have no machine for {project!r}. You do have one for each of: "
        f"{', '.join(others)}. Nothing was stopped, because a project name is what says which "
        "machine you meant and this verb will not guess at one that is billing."
    )


def terminate_argv(instance_id: str) -> tuple[str, ...]:
    """End one machine, releasing its volume with it.

    **TERMINATE AND NOT STOP, WHICH IS THE DECISION THIS VERB IS AND IS WORTH THE PARAGRAPHS.**
    The expiry janitor calls ``StopInstances`` and that is the right call *for the janitor*: it
    acts on a machine nobody asked it to touch, on a clock rather than on a person's word, so it
    takes the expensive half off the bill and leaves every recovery open. This verb is the
    person saying they are finished. Those are different acts and they get different calls.

    Three things make stop the wrong one here, and each is enough on its own.

    ``find_machine_argv`` looks for ``pending`` and ``running``, so **a stopped machine is
    invisible to ``edullm run`` and ``edullm shell``**: the next invocation does not find it and
    starts a second one. ``expiry_janitor._decide`` answers ``already_stopped`` for anything not
    running, so **nothing ever reclaims it**. And its root volume is two hundred gibibytes of
    gp3 that goes on billing while it sits there. A verb whose ordinary use leaves one of those
    behind each time is a leak that compounds silently, which is a worse failure than the hour
    of billing it was built to save.

    **AND THE VOLUME IS SCRATCH BY CONSTRUCTION RATHER THAN BY THIS CHOICE.** ``remote_script``
    syncs ``/work/<project>`` down from ``edullm-scratch`` before every ``edullm run`` and back
    up after it, and ``edullm shell`` prints in its own first lines that what survives the
    machine is the bucket. The durable thing is the prefix and the disk is a cache of it, so the
    machine is the part that is meant to be disposable. Terminating is that layout taken at its
    word; stopping preserves a copy of something the design already says is not the copy.

    It is also the more forgiving call to make twice. ``TerminateInstances`` on an
    already-terminated machine succeeds, where ``StopInstances`` on one refuses.
    """
    return (
        "aws",
        "ec2",
        "terminate-instances",
        "--instance-ids",
        instance_id,
        "--query",
        "TerminatingInstances[0].CurrentState.Name",
        "--output",
        "text",
    )


def priced_as(configuration: ReviewedConfiguration, instance_type: str) -> ComputeProfile | None:
    """The catalog entry for one instance type, or nothing where the catalog prices none.

    Keyed on the instance type because that is what EC2 reports and the profile name is on
    nothing the machine carries. Sorted by name where two profiles share a type, so which one
    is quoted does not depend on the order a file happens to be written in;
    :func:`instance_types_the_catalog_prices` already records that sharing is permitted.

    Nothing here is a refusal. A machine of a shape the catalog has stopped pricing is still a
    machine that has to be stoppable, and :func:`what_stopping_did` says the cost is unknown
    rather than declining to end it.
    """
    matching = sorted(
        (
            profile
            for profile in configuration.catalog.compute_profiles
            if profile.instance_type == instance_type
        ),
        key=lambda profile: profile.name,
    )
    return matching[0] if matching else None


def ran_for_said(ran: timedelta) -> str:
    """How long a machine was up, in the words somebody would use for it.

    Days and hours, or hours and minutes, or minutes -- never all three, and never seconds. The
    figure this decorates is approximate by construction, so a duration precise to the second
    beside it would be claiming an accuracy the money does not have.
    """
    minutes = max(int(ran.total_seconds() // 60), 0)
    if minutes < 1:
        return "less than a minute"
    days, rest = divmod(minutes, 60 * 24)
    hours, left = divmod(rest, 60)
    parts = [
        *([f"{days} day{'s' if days != 1 else ''}"] if days else []),
        *([f"{hours} hour{'s' if hours != 1 else ''}"] if hours else []),
        # Dropped once a machine has run for days: "2 days 3 hours 7 minutes" is three
        # numbers where the third cannot matter to anybody reading the first.
        *([f"{left} minute{'s' if left != 1 else ''}"] if left and not days else []),
    ]
    return " ".join(parts)


@dataclass(frozen=True)
class RanFor:
    """The interval a machine billed, and whether that interval is known or only bounded."""

    #: Its launch to the end of its running interval, which is the stop where there was one and
    #: now where there was not.
    ran: timedelta
    #: How long it has sat stopped since, where EC2 said when that began. ``None`` where the
    #: machine was still running, and where it was stopped and EC2 would not say when.
    stopped_for: timedelta | None
    #: Whether :attr:`ran` is the interval or only a ceiling on it.
    measured: bool


def ran_for(machine: LaneMachine, *, now: datetime) -> RanFor | None:
    """How long a machine was running, which is not how long ago it was launched.

    **EC2 BILLS THE RUNNING STATE AND NOTHING ELSE, SO THE STOP IS AN ENDPOINT AND NOT A
    DETAIL.** A machine the expiry janitor stopped goes on carrying a ``LaunchTime`` from hours
    earlier while it bills nothing at all, and for that machine the clock since its launch is
    the one number here certain to be wrong. ``StateTransitionReason`` is the other endpoint,
    and it arrives in the describe :func:`find_lane_machines_argv` already makes -- so the
    correct reading costs nothing over the wrong one, which is what settles whether to take it.

    **ONE RUNNING INTERVAL, BECAUSE NOTHING IN THIS BINARY CAN PRODUCE A SECOND ONE.** A machine
    stopped and started again has two, and a single stop instant would then describe only the
    last of them and understate -- so the assumption is checked rather than assumed, and what
    makes it hold is that no verb here ever starts a stopped machine. ``find_machine_argv``
    looks for ``pending`` and ``running``, so the reuse in ``edullm run`` and ``edullm shell``
    cannot see a stopped machine and starts a new one instead; :mod:`expiry_janitor` answers
    ``already_stopped`` and only ever calls ``StopInstances``; and no module under ``cli/``
    builds that argv at all, which ``tests/test_cli_stop.py`` holds by reading the parsed
    source. Adding one makes this paragraph a red test rather than a figure that quietly
    understates.

    **CLAMPED INTO THE LAUNCH AND NOW, WHICH COSTS ONE COMPARISON AND RULES OUT THE ABSURD
    ANSWER.** The two instants come from different fields written by different transitions and
    are subtracted against a laptop's clock, so a negative duration or one longer than the
    machine has existed is reachable without anything here being wrong. Either would print as a
    figure, and a negative one is a number nobody can act on at all.
    """
    if machine.launched is None:
        return None
    ended = min(max(machine.stopped or now, machine.launched), now)
    return RanFor(
        ran=ended - machine.launched,
        stopped_for=None if machine.stopped is None else now - ended,
        # A machine still running ends at now, and that is the measurement rather than a bound
        # on it. A stopped one whose transition carried no instant is the only case with
        # neither endpoint, and it is the only case quoted as a ceiling.
        measured=machine.state in ("pending", "running") or machine.stopped is not None,
    )


def _ran_up(rate_per_hour: Decimal, ran: timedelta) -> Decimal:
    """What a machine at one rate cost over one duration, to the cent.

    ``Decimal`` throughout and never a float, which is ``presentation.py``'s rule and this
    repository's: a rate of ``0.8048`` through binary floating point is not the number in
    ``config/workload-catalog.yaml``, and a figure a researcher compares against a bill has to
    be arithmetic they can repeat.
    """
    hours = Decimal(int(ran.total_seconds())) / Decimal(3600)
    return (rate_per_hour * hours).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def what_stopping_did(
    machine: LaneMachine,
    *,
    now: datetime,
    profile: ComputeProfile | None,
    uri: str,
    object_expiry_days: int,
) -> tuple[str, ...]:
    """What the researcher reads after their machine is gone: what it was, what it cost, and
    where their files are.

    **THREE FACTS AND ALL THREE ARE LOAD-BEARING.** A verb that said only "terminated" would
    leave somebody guessing at the bill they were trying to stop, and guessing at whether the
    work went with the disk. The one that is easy to leave out is the last: the scratch prefix
    survives the machine, which is the whole reason the layout puts the durable half in a
    bucket, and a person who has just watched an instance disappear has no way to know that
    unless it is said here.

    **THE COST IS QUOTED WITH WHAT IT IS AND IS NOT, RATHER THAN AS A NUMBER.** It is the
    catalog's on-demand rate against the hours the machine was running, so it excludes the
    volume and the traffic and it is a ceiling for a machine bought on Spot. ``AGENTS.md``
    forbids quoting a price from memory or from a document; this reads it out of reviewed
    configuration at the moment of asking and names the file it came from, which is the same
    discipline pointed at a figure rather than at a refusal.

    **THE HOURS IT RAN AND NOT THE HOURS SINCE IT LAUNCHED, WHICH IS WHAT THIS QUOTED UNTIL
    2026-08-06.** The first machine this verb met that had spent time stopped ran fifty-nine
    minutes and was told one hour twenty-four: the janitor had stopped it twenty-four minutes
    earlier, and EC2 bills no instance hour for a stopped machine. It erred high, which risks
    nothing and is not the point. The whole job of this sentence is saying what somebody spent,
    the sentence it opens with had already said the machine was stopped, and a reader who can
    see that the one number is wrong learns to skip it -- leaving a verb that has stopped
    working by the morning the number is large. :func:`ran_for` works the interval out, off a
    field the describe above already returns.
    """
    ran = ran_for(machine, now=now)
    return (
        (
            f"{machine.machine} is terminated, and it was {machine.state} until this ran. "
            + _what_it_ran_up(machine, ran=ran, profile=profile)
        ),
        (
            f"Your files are at {uri}, which survives the machine and holds what is in it "
            f"for {object_expiry_days} days. The machine's own disk went with the machine, "
            "which is what it was for: edullm run syncs that prefix down before your command "
            "and back up after it, so a new machine for this project picks up where this one "
            "left off."
        ),
    )


def _what_it_ran_up(
    machine: LaneMachine, *, ran: RanFor | None, profile: ComputeProfile | None
) -> str:
    """The money sentence, composed from what is actually known rather than written once.

    A cause and a sentence each, on the rule :meth:`LaneExpiry.said` states: a message two
    causes share is a message doing none of its job. An unreadable ``LaunchTime`` and a shape
    the catalog has stopped pricing are different reasons to have no figure; a machine bought
    on Spot has a figure that means something different from the same figure on demand; and a
    machine that spent time stopped has a figure smaller than its own clock, which earns a
    clause precisely because the reader can do that subtraction and would otherwise make the
    figure wrong.

    **THE MACHINE THAT NEVER STOPPED READS EXACTLY AS IT DID BEFORE.** Its clock and its running
    hours are the same number, so it earns no clause and its sentence is unchanged to the byte.
    Rewording the ordinary case would spend the settled shape of this message on a correction
    that does not apply to it.
    """
    if ran is None:
        return (
            f"EC2 did not say when it started, so how long it ran and what it cost cannot be "
            f"stated here. It was a {machine.instance_type}."
        )
    duration = ran_for_said(ran.ran)
    if profile is None:
        return (
            f"It ran {'' if ran.measured else 'at most '}{duration} on a "
            f"{machine.instance_type}, which config/workload-catalog.yaml no longer prices, so "
            "what it cost is not something this can say."
        )
    rate = serialize_decimal(profile.hourly_rate_usd)
    spent = serialize_decimal(_ran_up(profile.hourly_rate_usd, ran.ran))
    spot = (
        " It was bought on Spot, which bills under that rate, so read the figure as a ceiling."
        if machine.spot
        else ""
    )
    if not ran.measured:
        return (
            f"It ran at most {duration} on a {machine.instance_type}, which "
            f"config/workload-catalog.yaml prices as {profile.name} at ${rate}/hour, so at most "
            f"${spent}. EC2 did not say when it stopped, so that is the clock since it launched "
            f"rather than the hours it billed. That is the machine and not its disk or its "
            f"traffic.{spot}"
        )
    # Under a minute is a machine stopped while this verb was running, and a clause about it
    # would explain a subtraction that moves neither the duration nor the cent.
    stopped = (
        f" It then sat stopped for {ran_for_said(ran.stopped_for)}, which is not in that "
        "figure: EC2 bills no instance hour for a machine that is not running."
        if ran.stopped_for is not None and ran.stopped_for >= timedelta(minutes=1)
        else ""
    )
    return (
        f"It ran {duration} on a {machine.instance_type}, which config/workload-catalog.yaml "
        f"prices as {profile.name} at ${rate}/hour, so roughly ${spent}. That is the machine "
        f"and not its disk or its traffic.{stopped}{spot}"
    )


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
    **THE TAGS COME BACK WITH THE ID AND THAT IS WHAT REPAIRED THE EXPIRY.** This asked for
    ``InstanceId`` alone, so the one fact a reused machine carries that the verb needed --
    when the janitor may stop it -- was not in the answer, and the verb printed a fresh
    computation in its place. :func:`machine_already_running` reads it out of what comes back.
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
        "Reservations[].Instances[].{machine:InstanceId,tags:Tags}",
        "--output",
        "json",
    )


@dataclass(frozen=True)
class LaneSubnet:
    """One place a lane machine could go, and the zone that decides whether it can.

    The zone is carried beside the id rather than derived from it, because nothing about a
    subnet id says where it is and the only other way to find out is a second call. It is
    what :func:`subnets_to_try` filters on and what the refusal names, and both of those
    read better as a zone than as seventeen hex characters nobody recognises.
    """

    subnet: str
    zone: str


def find_subnets_argv() -> tuple[str, ...]:
    """Every subnet the platform's network declares, with the zone each one is in.

    **THE ZONE COMES BACK WITH THE ID AND THAT IS THE WHOLE OF THIS CHANGE AT THE WIRE.**
    This asked for ``Subnets[].SubnetId``, a bare list, and ``main`` took ``[0]`` of it. EC2
    answers that query in an order of its own that is stable for an account -- for this one it
    puts ``us-east-1f`` first -- so the lane pinned one zone by accident and asked it three
    times on the morning of 2026-08-06 when it had no ``g6.xlarge`` to sell. A list of ids
    cannot be filtered by zone or reported by zone, so both halves of the repair need this
    query rather than that one.

    ``{PLATFORM_NETWORK_NAME}-*`` still, so the network is discovered rather than pinned and a
    redeploy that moves an id moves the lane with it. That property was right and is untouched.
    """
    return (
        "aws",
        "ec2",
        "describe-subnets",
        "--filters",
        f"Name=tag:Name,Values={PLATFORM_NETWORK_NAME}-*",
        "--query",
        "Subnets[].{subnet:SubnetId,zone:AvailabilityZone}",
        "--output",
        "json",
    )


def zones_offering_argv(instance_type: str) -> tuple[str, ...]:
    """Which zones EC2 sells this instance type in at all, which is not every zone.

    **ASKED OF EC2 RATHER THAN READ OUT OF A FILE, AND THE FILE IS THE TEMPTING WRONG
    ANSWER.** ``infra/batch-network.yaml`` declares six subnets and its header spends four
    paragraphs on the one that is different: ``us-east-1e`` exists for ``p5`` and for nothing
    else, because ``p5.48xlarge`` and ``p5.4xlarge`` are the only shapes this repository backs
    that EC2 offers there. Its Name tag even says ``-p5-only``. A lane that tried all six for
    a ``g6.xlarge`` would spend one attempt on a zone that can never answer, and a lane that
    hard-coded five would be wrong the day a seventh subnet or a new shape arrives.

    ``tests/test_phase3_infrastructure.py`` already carries this rule for Batch, where getting
    it wrong is a job that sits in ``RUNNABLE`` for ever, and its own comment records the
    distinction this call keeps: the zone list is a fact about an instance type, not a fact
    about the network. Measured against this account on 2026-08-06, ``g6.xlarge`` is offered in
    1a, 1b, 1c, 1d and 1f, and ``p5.4xlarge`` in all six.

    Permitted to the lane's own credential: the researcher role's allow is ``*`` narrowed by
    denies that name ``ec2:RunInstances`` and no describe, and the call was made under an
    assumed ``edullm-researcher`` session on 2026-08-06 before this shipped.
    """
    return (
        "aws",
        "ec2",
        "describe-instance-type-offerings",
        "--location-type",
        "availability-zone",
        "--filters",
        f"Name=instance-type,Values={instance_type}",
        "--query",
        "InstanceTypeOfferings[].Location",
        "--output",
        "json",
    )


def lane_subnets(described: str) -> tuple[LaneSubnet, ...]:
    """:func:`find_subnets_argv`'s answer as the pairs it is, skipping anything malformed.

    An entry that is not an object, or that is missing either half, is skipped rather than
    unpacked, which is the rule :func:`machine_already_running` already follows and for the
    same reason: the shape is this module's own ``--query`` and the two ship together, so the
    only way to see something else is an install whose halves disagree, and an ``AttributeError``
    in front of a researcher is the one thing this binary promises not to do.

    Skipping reads as one fewer place to try, and skipping everything reads as no network at
    all, which ``main`` already reports as a deploy that has not happened. Neither invents a
    subnet, which is the failure that would matter.
    """
    found: list[LaneSubnet] = []
    for entry in json.loads(described.strip() or "[]"):
        if not isinstance(entry, dict):
            continue
        subnet = str(entry.get("subnet") or "")
        zone = str(entry.get("zone") or "")
        if subnet and zone:
            found.append(LaneSubnet(subnet=subnet, zone=zone))
    return tuple(found)


def zones_offering(described: str) -> frozenset[str]:
    """:func:`zones_offering_argv`'s answer as a set of zone names, empty where it said nothing.

    Empty is the answer for a call that failed as well as for a type nothing offers, and
    :func:`subnets_to_try` treats those the same way on purpose. See its own note.
    """
    return frozenset(
        str(entry) for entry in json.loads(described.strip() or "[]") if isinstance(entry, str)
    )


def subnets_to_try(
    subnets: Sequence[LaneSubnet], *, offered_in: frozenset[str]
) -> tuple[LaneSubnet, ...]:
    """Every place this shape could start, in an order chosen fresh for each caller.

    **THE FILTER FALLS THROUGH RATHER THAN EMPTYING THE LIST**, which is the rule
    :func:`default_compute_profile` already holds itself to. An empty ``offered_in`` is a
    ``describe-instance-type-offerings`` that failed or a type EC2 answered nothing for, and
    in both cases the honest move is to try every subnet and let ``RunInstances`` be the
    judge: an unusable zone costs one refusal that allocates nothing, and refusing to launch
    because a *describe* call did not answer would turn a hint into a gate.

    **SHUFFLED, AND THAT IS THE HALF THAT IS ABOUT THIRTY-FIVE PEOPLE RATHER THAN ABOUT ONE.**
    Ordering these deterministically fixes the outage and keeps the concentration that caused
    it: EC2 returns this account's subnets with ``us-east-1f`` first, so every researcher who
    typed the same command in the same hour asked the same zone for the same shape, and the
    second choice would merely be the next zone all of them pile into together. Nothing
    prefers a zone here -- ``infra/batch-network.yaml`` gives all six one route table, one
    internet gateway and one security group, and the scratch bucket is regional -- so the
    order is free to spread the demand, and spreading it is what stops one pool being asked
    thirty-five times a minute.

    Fresh per call rather than seeded, because a seed would be a second thing to get right for
    a property that only has to hold on average. ``tests/test_lane_zones.py`` pins it by
    drawing repeatedly and asserting more than one zone comes up first, which fails the moment
    somebody restores EC2's order.
    """
    candidates = [subnet for subnet in subnets if subnet.zone in offered_in] or list(subnets)
    random.shuffle(candidates)
    return tuple(candidates)


#: The EC2 error codes that mean *this zone*, right now, and that leave nothing behind.
#:
#: **THE LIST IS SHORT ON PURPOSE AND EVERY ADDITION HAS TO CLEAR THE SAME BAR: THE CALL
#: ALLOCATED NOTHING AND A DIFFERENT ZONE COULD ANSWER DIFFERENTLY.** Anything else retried
#: five times is five identical failures, five times the wait, and then a closing sentence
#: about capacity that is false. ``VcpuLimitExceeded`` is the case that makes the point and
#: ``config/capacity.yaml`` records it under ``gpu-8xa10g``: an account-wide quota looks
#: exactly like scarcity from underneath, it is the same in every zone, and what it needs is
#: a support ticket rather than another attempt. An authorization denial is the same shape.
#:
#: A refusal this does not recognise stops the loop, which is the safe direction for a reason
#: that has nothing to do with tidiness. A second ``RunInstances`` after an outcome nobody
#: could read is how one command leaves two machines billing, so "unrecognised" has to mean
#: "stop and say what EC2 said" rather than "try the next one".
#:
#: ``Unsupported`` is in the list because :func:`zones_offering_argv` can only fail open: when
#: that call did not answer, every subnet is a candidate and the ``us-east-1e`` one refuses
#: exactly this way. Measured on 2026-08-06 -- a ``g6.xlarge`` asked for in ``us-east-1e``
#: came back ``Unsupported`` in 1.27 seconds with nothing started.
ZONE_SHAPED_REFUSALS: Final[frozenset[str]] = frozenset(
    {"InsufficientInstanceCapacity", "Unsupported"}
)

#: How the AWS CLI spells an API error code in what it writes to stderr. The rest of that line
#: is prose AWS rewords, which is the thing ``AGENTS.md`` tells every caller not to match on.
#:
#: **THE DOT IS IN THE CLASS AND IT WAS NOT UNTIL 2026-08-06.** A whole family of EC2 codes is
#: spelled with one -- ``InvalidInstanceID.NotFound``, ``InvalidInstanceID.Malformed``,
#: ``InvalidGroup.NotFound`` -- and against the alphanumeric-only class this matched none of
#: them and answered ``None``. That is the safe direction for :func:`another_zone_may_answer`,
#: which is why it went unnoticed: an unreadable code stops the launch loop, which is what an
#: unreadable code should do. It is the wrong direction everywhere a code is read to recognise
#: one particular outcome, and ``edullm stop`` reads exactly one -- an instance EC2 no longer
#: has, which is the machine the expiry janitor got to first. Widening changes nothing about
#: the zone loop: no code in :data:`ZONE_SHAPED_REFUSALS` carries a dot, so a dotted code is
#: still not one a second zone could answer differently.
_AWS_ERROR_CODE = re.compile(r"An error occurred \(([A-Za-z][A-Za-z0-9.]*)\)")


def refusal_code(said: str) -> str | None:
    """The API error code out of what the AWS CLI printed, or nothing where there is none."""
    match = _AWS_ERROR_CODE.search(said)
    return match.group(1) if match else None


def another_zone_may_answer(said: str) -> bool:
    """Whether this refusal is one a different zone could answer differently.

    False for a refusal carrying no recognisable code at all, which includes an ``aws`` that
    could not be run and a call that timed out. See :data:`ZONE_SHAPED_REFUSALS` for why that
    direction is the safe one.
    """
    return refusal_code(said) in ZONE_SHAPED_REFUSALS


@dataclass(frozen=True)
class ZoneAttempt:
    """One zone asked for one machine, and exactly what it said back."""

    zone: str
    said: str

    @property
    def code(self) -> str:
        """The API error code, or a stand-in where AWS printed something with none in it."""
        return refusal_code(self.said) or "no error code"


def no_zone_had_this_shape(
    *, instance_type: str, profile: str, attempts: Sequence[ZoneAttempt], defaulted: bool
) -> str:
    """What to say when every zone that sells this shape refused to sell one now.

    **IT NAMES WHAT WAS TRIED RATHER THAN WHERE TO LOOK, AND THE DIFFERENCE IS MEASURED
    RATHER THAN STYLISTIC.** What this replaced was EC2's own sentence, which is better than
    most refusals in this binary -- it names the zone and it lists alternatives -- but the
    alternatives are not a capacity reading. They are the other zones the type is sold in,
    printed identically whether those zones are full or empty. On 2026-08-06 a ``p5.4xlarge``
    refused in 1a, 1b and 1c, and each refusal recommended the other two. A reader who took
    that list at its word would work through zones this loop had already been refused by.

    **AND IT SAYS THIS IS NOT THEM, BECAUSE THE FIRST THING A PERSON ASSUMES ABOUT THEIR FIRST
    COMMAND FAILING IS THAT IT IS THEM.** Everything else in the lane that stops with exit 3
    is a thing somebody could go and fix -- log in, deploy the network, install the plugin.
    This one is weather. Saying so plainly is the difference between somebody re-reading the
    guide for a flag they got wrong and somebody running the same command again in ten
    minutes, which is what actually works.

    The shape being defaulted is said where it was defaulted, for the reason
    :class:`DefaultedCompute` exists at all: a person who did not choose this shape cannot
    reason about it, and telling them they may name another is only useful if they know they
    did not name this one.
    """
    # Only reachable after a zone has refused, because the caller loops until one does not.
    # Asserted rather than defaulted: a sentence about zones that names none of them would be
    # a refusal saying less than the one it replaced.
    assert attempts
    zones = ", ".join(attempt.zone for attempt in attempts)
    codes = " and ".join(sorted({attempt.code for attempt in attempts}))
    chose = (
        f"{profile} is what this starts when no --compute is given, so it was chosen for you "
        "rather than by you. "
        if defaulted
        else ""
    )
    # The whole of one refusal, once. Five near-identical AWS paragraphs is a wall nobody
    # reads, and none at all leaves a reader with a code they cannot search for.
    last = attempts[-1]
    return (
        f"no zone had a {instance_type} to sell, so nothing started and nothing is billing. "
        f"This asked every zone EC2 offers {instance_type} in, one at a time, and all "
        f"{len(attempts)} refused: {zones}. Each answered {codes}. {chose}"
        "None of this is something you did or something about your account -- it is EC2 "
        f"having no {instance_type} to hand at this minute, which changes without anybody "
        "asking it to. Running the same command again in a few minutes is the usual remedy "
        "and needs nothing edited. --compute names a different shape if you would rather not "
        f"wait for this one. What EC2 said in {last.zone}: {last.said.strip()}"
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

    **ON-DEMAND UNLESS ASKED OTHERWISE, BECAUSE THE EXPIRY JANITOR HAS TO BE ABLE TO RECLAIM
    THE MACHINE.** A one-time Spot instance can only be terminated, so the plain form of Spot
    hands the sweep the one machine it cannot stop. ``--spot`` builds the persistent,
    stop-on-interrupt form, which is the one shape ``RunInstances`` will make that
    ``StopInstances`` accepts. ``decisions.md`` carries what was measured under "The lane runs
    On-Demand and --spot is the persistent stop form".

    This called itself a departure from ``system-overview.md`` until 2026-08-06, and that
    document now says the same thing in its own voice, so there is nothing here to flag. Kept
    as a note rather than deleted because the departure is the thing somebody who read the old
    version will come here looking for.
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

#: How a person in this organization gets an AWS session, spelled out rather than alluded to.
#:
#: There is one way and no second one. The sandbox issues no long-lived keys and refuses the
#: calls that would create one, so every human credential that has ever reached this account
#: came from the broker. A refusal saying "log in the way you normally do" is therefore
#: addressed to somebody who has already done it, and useless to the person meeting it for
#: the first time -- who is exactly the person a first `edullm run` puts in front of it.
#:
#: Held to ``guides/the-platform.md`` by ``tests/test_guides.py``, so the guide and the
#: refusal cannot name two different commands.
AWS_LOGIN_COMMAND: Final = "sb-aws-creds login"


def shell_session_argv(instance_id: str) -> tuple[str, ...]:
    """A shell on the machine, with no document named.

    The default is the account's own session preference, which is what somebody asking for a
    shell means. Nothing is opened, nothing is forwarded and no key exists.
    """
    return ("aws", "ssm", "start-session", "--target", instance_id)


def _session_parameters(values: Mapping[str, object]) -> str:
    """A ``--parameters`` document, serialised rather than interpolated.

    **EVERY SESSION PARAMETER GOES THROUGH HERE AND NONE IS BUILT WITH AN F-STRING.** One was,
    and it is the defect that made ``edullm run`` fail on every command it was ever given:
    ``remote_script`` ends in ``echo "edullm-exit:$status"``, the two quotes went into the
    document unescaped, and what reached the AWS CLI was not JSON. The verb then reported that
    the session had ended without saying what the command did, which is true and names neither
    the quote nor the file. A researcher's own command carries the same hazard and carries it
    further, because ``python -c "print(1)"`` is a thing people type.
    """
    return json.dumps(values, separators=(",", ":"))


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
        _session_parameters(
            {
                "portNumber": [str(settings.notebook_port)],
                "localPortNumber": [str(local_port)],
            }
        ),
    )


def command_line(tokens: Sequence[str]) -> str:
    """The tokens after a bare ``--``, back as one line of shell that means the same thing.

    **A PLAIN ``" ".join`` LOSES THE QUOTING THE RESEARCHER'S OWN SHELL ALREADY TOOK OFF, AND
    THAT IS NOT A CORNER CASE.** ``edullm run -- python -c 'print(1+1)'`` arrives here as three
    tokens, the third being ``print(1+1)`` with no quotes left on it, and joined with a space it
    reaches the machine as ``python -c print(1+1)`` -- where the parentheses are shell syntax
    and bash refuses the whole script before anything runs. Measured on 2026-08-06; the machine
    printed a bash parse error naming the entire remote script and the verb reported only that
    the session had ended without saying what the command did.

    ``shlex.join`` puts back exactly the quoting that makes each token one word again, which is
    the property the researcher had when they typed it and expects to still have.
    """
    return shlex.join(tokens)


def under_a_shell(script: str) -> str:
    """One line of shell, as something ``AWS-StartNonInteractiveCommand`` will actually run.

    **THAT DOCUMENT RUNS NO SHELL, WHICH IS THE SECOND REASON ``edullm run`` NEVER WORKED.** It
    splits the command it is given the way a shell splits a line, honouring quotes, and then
    executes the first token with the rest as its arguments. Nothing interprets ``;``, ``$?``,
    ``(`` or a redirection. Handed ``remote_script``'s line directly it ran ``echo`` with the
    whole of the rest of the pipeline as arguments to it, printed them back as one line, and
    exited 0 -- so the sentinel never appeared, the machine did none of the work, and the verb
    reported only that the session had ended without saying what the command did. Measured
    against this account on 2026-08-06.

    ``shlex.quote`` and not an f-string with quotes around it. The script already contains a
    double quote, of its own, and a researcher's command is arbitrary text that may contain
    either kind; ``'`` inside a single-quoted word is the one case a hand-rolled wrapper always
    gets wrong, and it is what ``git commit -m 'don't'`` produces.
    """
    return f"bash -c {shlex.quote(script)}"


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
        _session_parameters({"command": [under_a_shell(command)]}),
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


#: What AWS's own SSH-over-Session-Manager instructions put in front of the command on Windows,
#: down to the absolute path. The interpreter is named in full rather than as ``powershell``
#: because a ``ProxyCommand`` is not resolved against ``PATH`` by every SSH client that reads one,
#: and this path is present on every supported Windows.
WINDOWS_PROXY_INTERPRETER: Final = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"


def ssh_proxy_command(instance_id: str, *, system: str) -> str:
    """The one line that makes an editor over SSH work, for the laptop it is printed on.

    VS Code Remote-SSH and plain ``ssh`` both drive a ``ProxyCommand``, and this is the documented
    one for Session Manager. It is printed because ``~/.ssh/config`` is a file this binary does not
    own and may not be the only thing writing to.

    **THERE ARE TWO OF THESE BECAUSE AWS DOCUMENTS TWO, AND PRINTING ONLY THE FIRST HANDED EVERY
    WINDOWS RESEARCHER A LINE THAT CANNOT RUN.** The Unix line wraps the command in ``sh -c``, and
    native Windows has no ``sh``: not in ``System32``, not from the OpenSSH client Windows ships,
    and not from anything a person who has only installed the AWS CLI has. The failure is at
    connect time, inside whatever editor read the config, and what it says is that ``sh`` was not
    found -- which sends somebody to look at their SSH configuration for a program this platform
    never told them they needed.

    So ``system`` decides, and it is the operating system this process is running on, which is the
    one the file being pasted into belongs to. Both spellings are AWS's own, from *Step 8: Allow
    and control permissions for SSH connections* in the Systems Manager user guide, which gives
    the ``sh -c`` form under **Linux and macOS** and the ``powershell.exe`` form under **Windows**.
    Neither is invented here, which matters for a line this binary cannot test by running.

    **WSL TAKES THE UNIX LINE AND THAT IS NOT AN OVERSIGHT.** A researcher there is a Linux
    process, writing a Linux ``~/.ssh/config``, read by a Linux ``ssh`` that has ``sh``.
    :func:`platform.system` answers ``Linux`` for them, so they get the form that works, and the
    separate hazard of a *Windows* ``gh`` on a WSL ``PATH`` is :func:`github_interop_diagnostic`'s
    and is diagnosed there.

    ``system`` is a parameter rather than a call inside this function so that both lines are
    reachable from a suite on either kind of laptop. Compared case-folded, which is the comparison
    :mod:`edullm_platform.cli.workspace` makes for the same reason it does there.
    """
    session = (
        f"aws ssm start-session --target {instance_id} "
        "--document-name AWS-StartSSHSession --parameters portNumber=%p"
    )
    if system.casefold() == WINDOWS:
        return f'ProxyCommand {WINDOWS_PROXY_INTERPRETER} "{session}"'
    return f'ProxyCommand sh -c "{session}"'


#: Where AWS publishes every build of the plugin. One base, because the five installers below
#: differ only in the last two segments, and a second copy of the host is a URL that can rot in
#: one place and not the other.
PLUGIN_DOWNLOADS: Final = "https://s3.amazonaws.com/session-manager-downloads/plugin/latest"

#: What :func:`platform.system` answers on a Mac, compared case-folded for the reason
#: :data:`edullm_platform.cli.workspace.WINDOWS` is: it is the only comparison an injected
#: value cannot get subtly wrong.
MACOS: Final = "darwin"

#: What :func:`platform.machine` answers on a 64-bit ARM laptop. Two spellings and not one:
#: Darwin says ``arm64`` and Linux says ``aarch64`` for the same silicon, and a check for
#: either alone hands half the ARM machines here an x86 package that installs and then will
#: not run.
ARM_MACHINES: Final = frozenset({"arm64", "aarch64"})


def _is_arm(machine: str) -> bool:
    return machine.strip().casefold() in ARM_MACHINES


def plugin_install_commands(*, system: str, machine: str, has_dpkg: bool = False) -> tuple[str, ...]:
    """What AWS documents installing the plugin on this exact machine, verbatim.

    **THESE ARE COPIED FROM AWS AND NOT COMPOSED, WHICH IS THE PROPERTY THAT MATTERS ABOUT
    THEM.** Every line below is character for character what the Systems Manager user guide
    gives under *Install the Session Manager plugin* for that operating system, read on
    2026-08-06: the ``.exe`` for Windows, the signed ``.pkg`` and its two commands for macOS,
    the ``.deb`` and ``dpkg`` for Debian and Ubuntu, and the ``.rpm`` one-liner for Amazon
    Linux and RHEL. Nothing here is a package manager AWS does not document -- in particular
    **no Homebrew formula is named**, because AWS documents none and a formula invented here
    would be a guess printed to somebody with no way to check it.

    **RETURNED AS LINES RATHER THAN AS PROSE, AND THAT IS WHAT MAKES THEM USABLE.**
    ``presentation.render_refusals`` wraps a detail with ``textwrap.wrap``, so a shell command
    carried inside one arrives broken across four indented lines -- copyable only by
    reassembling it, which is most of the work this whole change exists to remove. A URL
    survives that treatment because it is one token and ``break_long_words`` is off; ``curl
    "..." -o "..."`` does not. So the caller prints these as they are, one per line, beneath
    the wrapped paragraph.

    **THE ARCHITECTURE IS READ AND NOT OFFERED.** AWS publishes an x86 and an ARM build of
    every one of these and the process already knows which it is, so printing both would be
    handing the reader a decision that has been made.

    ``has_dpkg`` is measured by the caller for the reason every other input to this module is:
    this file runs no process, so it cannot ask a Linux box which packaging it uses. Debian
    and Ubuntu get the ``.deb``; everything else gets the ``.rpm``, which is the only other
    family AWS publishes a package for.
    """
    named = system.strip().casefold()
    if named == WINDOWS:
        return (f"{PLUGIN_DOWNLOADS}/windows/SessionManagerPluginSetup.exe",)
    if named == MACOS:
        build = "mac_arm64" if _is_arm(machine) else "mac"
        return (
            (
                f'curl "{PLUGIN_DOWNLOADS}/{build}/session-manager-plugin.pkg"'
                ' -o "session-manager-plugin.pkg"'
            ),
            "sudo installer -pkg session-manager-plugin.pkg -target /",
            (
                "sudo ln -s /usr/local/sessionmanagerplugin/bin/session-manager-plugin"
                " /usr/local/bin/session-manager-plugin"
            ),
        )
    if has_dpkg:
        build = "ubuntu_arm64" if _is_arm(machine) else "ubuntu_64bit"
        return (
            (
                f'curl "{PLUGIN_DOWNLOADS}/{build}/session-manager-plugin.deb"'
                ' -o "session-manager-plugin.deb"'
            ),
            "sudo dpkg -i session-manager-plugin.deb",
        )
    build = "linux_arm64" if _is_arm(machine) else "linux_64bit"
    return (f"sudo dnf install -y {PLUGIN_DOWNLOADS}/{build}/session-manager-plugin.rpm",)


def _plugin_install_said(system: str) -> str:
    """The sentence in front of the commands, which is only long on the platform that needs it.

    **WINDOWS GETS THREE CLAUSES NOBODY ELSE GETS AND EVERY ONE OF THEM IS AWS'S OWN
    WARNING.** The installer needs Administrator rights. Windows usually does not hand the
    new ``PATH`` entry to the shell that ran it, which AWS carries a whole troubleshooting
    topic for -- so the most likely event after a successful install is this same refusal in
    the same window, and somebody not told that reads a working installation as a broken one.
    And AWS supports the plugin under PowerShell and the Command shell only.

    **THE SHELL CLAUSE IS KEPT RATHER THAN DROPPED AS TRIVIA, AND THE REASON IS THAT IT
    SHARES A SYMPTOM WITH THE ONE ABOVE IT.** Both present as a plugin that is installed and
    still will not work, from different causes and with different repairs, in a population
    that has every reason to be sitting in Git Bash: this binary drives ``git`` and ``gh``,
    so a Git Bash window is exactly where somebody already is when they type the verb.
    Naming one of two causes for one symptom sends half the readers to the wrong fix.

    Nowhere else gets a caveat, because AWS documents none for them and prose nobody needs is
    prose that pushes the commands off the screen.
    """
    if system.strip().casefold() == WINDOWS:
        return (
            "Download and run AWS's installer, which needs Administrator rights and installs "
            "to %PROGRAMFILES%\\Amazon\\SessionManagerPlugin\\bin\\. Then run this again from "
            "a new PowerShell or Command Prompt window rather than the one you installed "
            "from: Windows usually does not give the new PATH entry to the shell that ran the "
            "installer, which is the likeliest way a working install goes on looking like "
            "this refusal. AWS supports the plugin under PowerShell and the Command shell "
            "only, so run edullm from one of those rather than from Git Bash."
        )
    return "AWS documents this for the machine you are on, and it is the whole of it."


def missing_plugin_refusal(*, system: str, machine: str, has_dpkg: bool = False) -> Refusal:
    """What to say when the laptop has the AWS CLI and not the piece that carries a session.

    **THIS PRINTED "INSTALL IT FROM THE AWS DOCUMENTATION" UNTIL 2026-08-06, WHICH IS A
    SEARCH ENGINE WITH EXTRA STEPS.** It named the cause, said nothing was billing, and then
    asked somebody on their first morning to go and find a page. The process knows which
    operating system and which silicon it is on, so it can name the one command that person
    needs rather than the five AWS publishes, and it does.

    **THE ORDERING SENTENCE IS HERE AND NOT IN THE OTHER REFUSAL, AND THAT IS THE POINT OF
    PUTTING IT IN THIS ONE.** ``cli/main.py``'s ``_lane_session`` checks the plugin before it
    asks AWS who you are, because the plugin check is local and this binary answers locally
    before it reaches out. So this refusal is the first of the two a newcomer meets and the
    credentials one is the second, which means somebody who installs the plugin and believes
    they are finished is about to meet another wall. Naming the next one here costs a
    sentence. The reverse would be pointless: reaching ``_no_aws_session`` at all is proof
    the plugin is already on PATH.

    **THE COMMANDS ARE NOT IN THE DETAIL, AND THAT IS THE ONE THING TO NOT TIDY BACK.**
    ``render_refusals`` wraps this string, which breaks a shell command across four indented
    lines. :func:`plugin_install_commands` hands the caller the lines to print underneath
    unwrapped instead, so there is exactly one copy of each command and it is pasteable.

    ``system`` and ``machine`` are handed in rather than read, which is the arrangement
    ``cli/workspace.py`` already uses for ``platform.system()``: it is what lets all five
    installers be asserted from one laptop, and Windows is the one this repository has never
    been able to test on.
    """
    return Refusal(
        code="session_plugin_missing",
        detail=(
            f"{SESSION_PLUGIN} is not on PATH. A lane session is a Systems Manager session rather "
            "than SSH, which is what means there is no key to hold and no port open on the "
            "machine, and the plugin is the piece of that which runs on your laptop. Nothing was "
            "started and nothing is billing. There are two prerequisites and this is the first "
            f"of them: an AWS session is what this checks next, so `{AWS_LOGIN_COMMAND}` is the "
            "step after this one rather than an alternative to it. "
            + _plugin_install_said(system)
        ),
    )


def remote_script(*, uri: str, project: str, command: str) -> str:
    """What runs on the machine for one ``edullm run``, as one line of shell.

    Three acts and the middle one is the researcher's. Sync the tree down, run what was asked,
    sync back whatever it wrote. The status is captured between the second and the third, so a
    command that failed still gets its output carried up, and it is printed last on a line the
    verb parses, because ``start-session`` exits with the plugin's status rather than the remote
    command's.

    **THE DIRECTORY IS MADE WITH ``sudo`` AND HANDED OVER, AND A PLAIN ``mkdir -p`` HERE DOES
    NOT WORK.** A Session Manager session runs as ``ssm-user``, who cannot create a directory at
    the filesystem root, so the first act failed ``Permission denied``, the sync down had
    nowhere to land, the ``cd`` failed, and the sync back reported a path that does not exist --
    while the researcher's own command ran anyway, in whatever directory the session started in,
    and returned 0. A run that half-works and says it succeeded is worse than one that refuses.
    ``ssm-user`` is in the AMI's sudoers with no password, which is what the agent puts there.

    ``install -d`` rather than ``mkdir`` and a ``chown``: one call, it makes the parents, and it
    leaves the directory owned by the session rather than by root, so the sync back and anything
    ``edullm shell`` does later in the same place need no further privilege.
    """
    directory = f"/work/{project}"
    return (
        f'set -u; sudo install -d -o "$(id -u)" -g "$(id -g)" {directory}; '
        f"aws s3 sync {uri} {directory} --only-show-errors; "
        f"cd {directory}; "
        f"({command}); status=$?; "
        f"aws s3 sync {directory} {uri} --only-show-errors; "
        f'echo "edullm-exit:$status"'
    )
