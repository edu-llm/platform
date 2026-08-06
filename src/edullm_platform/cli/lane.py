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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final, Literal

from pydantic import Field

from edullm_platform.cli.configuration import (
    ConfigurationUnreadableError,
    ReviewedConfiguration,
)
from edullm_platform.cli.preflight import Refusal
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
    "AWS_LOGIN_COMMAND",
    "GPU_AMI_PARAMETER",
    "LANE_INSTANCE_PROFILE",
    "LANE_TAG_KEY",
    "PLATFORM_NETWORK_NAME",
    "SCRATCH_BUCKET",
    "SESSION_PLUGIN",
    "ZONE_SHAPED_REFUSALS",
    "DefaultedCompute",
    "LaneExpiry",
    "LaneRequest",
    "LaneSubnet",
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
    "find_machine_argv",
    "find_subnets_argv",
    "instance_type_for",
    "lane_refusals",
    "lane_subnets",
    "load_working_tier_settings",
    "machine_already_running",
    "missing_plugin_refusal",
    "no_zone_had_this_shape",
    "notebook_forward_argv",
    "person_from_caller_arn",
    "placement_said",
    "placement_verdict",
    "placement_warning",
    "refusal_code",
    "remote_command_argv",
    "remote_script",
    "run_instances_argv",
    "shell_session_argv",
    "ssh_proxy_command",
    "subnets_to_try",
    "under_a_shell",
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
_AWS_ERROR_CODE = re.compile(r"An error occurred \(([A-Za-z][A-Za-z0-9]*)\)")


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
