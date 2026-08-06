"""What ``edullm studio`` decides without an account, which is nearly all of it.

THIS MODULE MAKES NO AWS CALL AND RUNS NO PROCESS, which is ``cli/lane.py``'s arrangement and
is here for the same reason: it answers what a person's space is called, what a shape costs,
which tags a space and an app must carry and the exact argv of every command the verb runs, and
``main.py`` runs them. The whole verb is therefore testable with no credential.

**WHY THERE IS A STUDIO VERB AT ALL, SINCE THE LANE ALREADY STARTS A GPU.** The two overlap and
the owner said so. Three of the four advantages the lane was argued to have over Studio do not
survive contact: Studio can use the same instance types, Studio can clone a repository, and cost
visibility is a tagging problem rather than an architectural one. Reproducibility was never a
reason to prefer the lane for *exploration* -- nobody re-runs a prototype, and the run somebody
cites goes through ``submit``, which is neither surface. What Studio wins on is what people
feel: a disk that survives, no Session Manager plugin, a notebook that reads as a document, and
a web UI. So Studio is the exploration surface and this verb's whole job is to make it easy to
reach and impossible to forget about.

**WHAT SURVIVES ON THE LANE'S SIDE IS ``edullm run``**, which Studio has no equivalent for: ship
a working tree to a GPU, run one command, stream it back, discard the machine. ``edullm shell``
stays as the way onto the exact machine shape somebody is about to submit to. ``edullm shell
--notebook`` is the one Studio plainly does better, and nothing here removes it.

**A SPACE IS SCOPED TO A PERSON BY STUDIO'S OWN MODEL AND NOT BY A CONVENTION THIS INVENTS.** A
*user profile* is the person -- ``CreatePresignedDomainUrl`` takes one and signs that person in
-- and a space carries ``OwnershipSettings.OwnerUserProfileName`` with
``SpaceSharingSettings.SharingType`` of ``Private``, which is what makes it one person's. That
is what those two fields exist for. The domain supports it as it stands: ``AuthMode`` is
``IAM``, ``StudioWebPortal`` is ``ENABLED``, ``ExecutionRoleSessionNameMode`` is
``USER_IDENTITY`` so the session carries who opened it, and ``DefaultSpaceSettings.ExecutionRole``
is set, which is the setting a private space needs to launch without being configured one at a
time. Both spaces that existed before this verb are already private and owned by a profile, so
the pattern is in use rather than proposed.

**NOTHING HERE WRITES AN ``ExpiresAt`` TAG AND THE OMISSION IS DELIBERATE.**
``infra/expiry-janitor.yaml`` sweeps EC2 instances by that tag and has no SageMaker arm, so an
``ExpiresAt`` on a Studio app would be a promise nothing in this repository keeps -- worse than
no tag, because the next reader would find it and conclude a machine was being watched. Studio
is stopped by ``--stop`` and by nothing else, and the verb says exactly that out loud.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Final, Literal, Self

from pydantic import BeforeValidator, Field, model_validator

from edullm_platform.cli.preflight import Refusal
from edullm_platform.contracts.base import (
    ContractModel,
    PositiveStrictDecimal,
    require_ordered_sequence,
)
from edullm_platform.researcher_lane import PROJECT_TAG_KEY
from edullm_platform.reviewed_configuration import ConfigFile, load_config_file

__all__ = [
    "APP_NAME",
    "APP_TYPE",
    "IMAGE_ACCOUNT_PARAMETER",
    "PERSON_TAG_KEY",
    "PRESIGNED_URL_SECONDS",
    "STUDIO_CONFIG_FILE",
    "STUDIO_NAME_LIMIT",
    "SURFACE_TAG_KEY",
    "SURFACE_TAG_VALUE",
    "RunningApp",
    "StudioRequest",
    "StudioSettings",
    "StudioShape",
    "already_running_said",
    "could_not_resolve_the_image",
    "create_app_argv",
    "create_space_argv",
    "create_user_profile_argv",
    "delete_app_argv",
    "describe_app_argv",
    "describe_space_argv",
    "describe_user_profile_argv",
    "image_account_argv",
    "image_arn_for",
    "landing_uri",
    "load_studio_settings",
    "nothing_to_stop",
    "presigned_url_argv",
    "price_said",
    "running_app",
    "shape_for",
    "studio_document",
    "studio_name_for",
    "studio_refusals",
    "studio_tags",
    "unpriced_shape",
    "unstopped_said",
]

#: Where the domain and the rate card are written down, as a person would type the path.
#:
#: Re-exported off :class:`~edullm_platform.reviewed_configuration.ConfigFile` rather than typed,
#: so a refusal that names the file to edit cannot name a path that has moved. ``main.py`` reads
#: this rather than importing the enum, which keeps one module knowing where Studio's numbers
#: live.
STUDIO_CONFIG_FILE: Final = f"config/{ConfigFile.STUDIO.value}"

#: The one app every space gets. Studio permits several per space and this verb starts one,
#: because a second app on one space is a second bill against one person's name with nothing in
#: the verb able to say which of them anybody is using. ``default`` is Studio's own name for the
#: first one and is what the console creates, so a space this verb made and a space somebody
#: made by hand are the same shape.
APP_NAME: Final = "default"

#: JupyterLab and not CodeEditor or the retired JupyterServer. It is the notebook-as-a-document
#: surface that the argument for preferring Studio rests on, and it is what both existing spaces
#: run.
APP_TYPE: Final = "JupyterLab"

#: Who this space belongs to, carrying the same person string the working tier's prefix uses.
#:
#: Prefixed, so it stays clear of ``researcher_lane.GOVERNANCE_TAG_KEYS`` -- those are the keys
#: the researcher role forbids stripping after an EC2 launch, and a Studio tag caught by that
#: deny would be one this verb could not correct.
PERSON_TAG_KEY: Final = "edullm:person"

#: Which surface spent the money, so Cost Explorer can tell an exploration hour from a lane hour
#: from a Batch hour once these keys are activated. Without it a reader grouping by ``Project``
#: sees one figure per project and no way to ask which of three routes produced it.
SURFACE_TAG_KEY: Final = "edullm:surface"
SURFACE_TAG_VALUE: Final = "studio"

#: How long a user profile or space name may be, and it is SageMaker's number rather than a
#: choice. Measured against the live API rather than read out of a document: the service answers
#: ``Member must satisfy regular expression pattern: [a-zA-Z0-9](-*[a-zA-Z0-9]){0,62}``, which is
#: one leading character plus sixty-two.
STUDIO_NAME_LIMIT: Final = 63

#: Everything SageMaker will not take in a name. **THIS IS THE SEAM BETWEEN THE TWO SURFACES AND
#: IT IS NARROWER THAN IT LOOKS.** ``cli/lane.py``'s ``person_from_caller_arn`` permits ``.`` and
#: ``_`` because an S3 prefix segment does, and the broker mints ``broker-frank.gonzalez-<epoch>``
#: -- so the person string the working tier uses is ``frank.gonzalez``, which SageMaker rejects
#: outright. Substituting rather than refusing is safe here for a reason worth stating: two
#: people collide only if their names differ solely in the characters being replaced, and a
#: roster of ``first.last`` logins has no such pair. A collision would be two people sharing one
#: private space, so :func:`studio_refusals` would rather be wrong loudly than quietly, and
#: ``tests/test_studio.py`` holds the roster against it.
_UNSAFE_IN_A_STUDIO_NAME: Final = re.compile(r"[^A-Za-z0-9]+")

#: Leading and trailing dashes, which the pattern above can produce and SageMaker refuses. The
#: name must open and close on an alphanumeric.
_EDGE_DASHES: Final = re.compile(r"^-+|-+$")

#: Where Amazon publishes the account its SageMaker distribution images live in.
#:
#: **READ AT RUN TIME RATHER THAN WRITTEN DOWN, AND THE REASON IS NOT ONLY THE SECRET SCANNER.**
#: ``tests/test_evidence.py`` refuses any 12-digit run in the tracked tree and does not try to
#: judge whose account an id belongs to, which is the right shape for that guard -- so the
#: account segment of an image ARN cannot be a literal here. The parameter is also regional and
#: AWS's to move, so reading it is how the ARN stays correct without anybody noticing it has
#: changed. ``cli/lane.py``'s ``GPU_AMI_PARAMETER`` is the same arrangement for the same reasons.
IMAGE_ACCOUNT_PARAMETER: Final = "/aws/service/sagemaker-distribution/ecr-account-id"

#: How long a presigned URL is good for. Sixty seconds is the default and this asks for more,
#: because the URL is printed into a terminal for somebody to click and the gap between printing
#: it and reading it is a person's attention rather than a machine's. Five minutes is the API's
#: own default and there is no reason to be stricter than the service.
PRESIGNED_URL_SECONDS: Final = 300


class StudioShape(ContractModel):
    """One shape the verb will start, and the list price of an hour of it."""

    instance_type: str = Field(min_length=1)
    #: The image's name and not its ARN. :func:`image_arn_for` composes the ARN, because its
    #: account segment is Amazon's rather than this account's and a 12-digit literal anywhere
    #: in the tracked tree is refused by ``tests/test_evidence.py``.
    image_name: str = Field(min_length=1)
    hourly_rate_usd: PositiveStrictDecimal
    accelerator: Literal["cpu", "gpu"]


class StudioSettings(ContractModel):
    schema_version: Literal[1]
    domain_id: str = Field(min_length=1)
    region: str = Field(min_length=1)
    volume_gib: int = Field(gt=0)
    volume_gib_month_usd: PositiveStrictDecimal
    default_instance_type: str = Field(min_length=1)
    #: ``strict=False`` and a ``BeforeValidator``, which is ``WorkloadCatalog``'s arrangement
    #: and is here for the same reason. ``ContractModel`` is strict, so a YAML list does not
    #: become a tuple on its own; and a mapping is a sequence to pydantic's coercion but not to
    #: a reader, so ``require_ordered_sequence`` refuses one rather than admitting a shape whose
    #: order is a fact about a dict.
    shapes: Annotated[tuple[StudioShape, ...], BeforeValidator(require_ordered_sequence)] = Field(
        min_length=1, strict=False
    )

    @model_validator(mode="after")
    def validate_shapes(self) -> Self:
        """The default has to be one of the shapes, and no type may be priced twice.

        Both are the same failure seen from two sides: a verb that cannot find a price for
        what it is about to start. Caught here rather than at the call site, because the call
        site is one line before an app that costs money and a refusal there reads as the
        person having done something wrong.
        """
        named = [shape.instance_type for shape in self.shapes]
        if len(set(named)) != len(named):
            raise ValueError("an instance type is priced twice, so its rate is ambiguous")
        if self.default_instance_type not in named:
            raise ValueError(
                f"default_instance_type {self.default_instance_type!r} is not one of the "
                "shapes, so a bare edullm studio could not be priced"
            )
        return self


def load_studio_settings(directory: Path | None = None) -> StudioSettings:
    """The domain and the rate card, out of a resolved directory rather than a written path.

    The default resolution is :mod:`edullm_platform.reviewed_configuration`'s, which is the
    correction ``load_working_tier_settings`` records at length: a path relative to the
    working directory resolved for the test suite and for nobody else, because this verb is
    used from a research checkout and never from a platform one.
    """
    return load_config_file(ConfigFile.STUDIO, StudioSettings, directory=directory)


@dataclass(frozen=True)
class StudioRequest:
    """What the verb was asked for, after the flags and the caller identity are merged.

    Three fields, and one of them is derived. ``person`` is what the caller ARN says and
    ``studio_name`` is that same person spelled the way SageMaker will accept, held beside it
    rather than recomputed, because the two names appear in different places -- the person in
    a tag and in the working tier, the Studio name in a user profile, a space and a URL -- and
    a second derivation is a second chance for them to disagree.
    """

    person: str
    studio_name: str
    project: str


def studio_name_for(person: str) -> str:
    """One person's name as SageMaker will take it, or the empty string where it cannot.

    Empty rather than a raise, because the caller has a refusal to render and a traceback in
    front of a researcher is the one thing this binary promises not to produce.
    """
    collapsed = _UNSAFE_IN_A_STUDIO_NAME.sub("-", person)
    trimmed = _EDGE_DASHES.sub("", collapsed)[:STUDIO_NAME_LIMIT]
    return _EDGE_DASHES.sub("", trimmed)


def studio_refusals(request: StudioRequest) -> tuple[Refusal, ...]:
    """Everything this verb refuses, which is two things and is the whole list.

    **NEITHER IS A PERMISSION, WHICH IS THE TEST ``cli/lane.py`` SETS AND THIS INHERITS.** One
    says the caller cannot be named and the other says a destination cannot be spelled. Studio
    is the exploration surface: nothing here is checked against the registry, priced against a
    policy, approved or written to a lineage record, and a refusal that withheld a shape from
    a person would be the submission path arriving by the back door.

    ``--project`` is required and this is the same argument ``lane_refusals`` makes for it,
    only harder. There it names a working prefix; here it is the entire reason the tagging
    work exists, because a Studio app with no project tag is an undifferentiated line in Cost
    Explorer and that is precisely the hazard in pointing thirty-five people at Studio.
    """
    refusals: list[Refusal] = []
    if not request.person:
        refusals.append(
            Refusal(
                code="cannot_tell_who_you_are",
                detail=(
                    "this session is already inside the lane, and sts:GetCallerIdentity does "
                    "not return the source identity, so which person's space to open cannot "
                    "be read from it. Run this from your ordinary session."
                ),
            )
        )
    elif not request.studio_name:
        refusals.append(
            Refusal(
                code="studio_name_is_unusable",
                detail=(
                    f"{request.person!r} has nothing left in it once the characters SageMaker "
                    "refuses in a user profile name are removed. The service takes "
                    "[a-zA-Z0-9](-*[a-zA-Z0-9]){0,62} and nothing else. File this with "
                    "edullm ask, because the fix is a name this tool does not get to choose."
                ),
            )
        )
    if not request.project:
        refusals.append(
            Refusal(
                code="no_project",
                detail=(
                    "--project names what this space is for. It tags the space and the app, "
                    "and it is what cost attribution reads. There is no default, because a "
                    "default would put two unrelated pieces of work under one name and one "
                    "bill -- and Studio spend is the spend nothing else on this platform can "
                    "currently see."
                ),
            )
        )
    return tuple(refusals)


def shape_for(settings: StudioSettings, instance_type: str | None) -> StudioShape | None:
    """The priced shape somebody asked for, or the default, or nothing where it is not priced.

    ``None`` is "the rate card does not carry this", which the verb refuses on. It is never
    "start it anyway at a price nobody knows", which is the whole argument for the allow-list
    in ``config/reports/studio.yaml``.
    """
    wanted = instance_type or settings.default_instance_type
    return next((shape for shape in settings.shapes if shape.instance_type == wanted), None)


def image_account_argv() -> tuple[str, ...]:
    """Ask AWS which account its distribution images live in.

    One ``ssm:GetParameter`` against a public parameter, which the boundary does not deny and
    which needs no permission beyond the lane's. It is made once per start and never on
    ``--stop``, which reaches no image at all.
    """
    return (
        "aws",
        "ssm",
        "get-parameter",
        "--name",
        IMAGE_ACCOUNT_PARAMETER,
        "--query",
        "Parameter.Value",
        "--output",
        "text",
    )


def image_arn_for(settings: StudioSettings, shape: StudioShape, *, account: str) -> str:
    """The image ARN ``create-app`` demands, composed from the three parts that make it.

    The region is the domain's rather than the caller's shell's, because an ARN naming a
    region the domain is not in resolves to nothing and the failure reads as a missing image
    rather than as a laptop pointed somewhere else.
    """
    return f"arn:aws:sagemaker:{settings.region}:{account}:image/{shape.image_name}"


def could_not_resolve_the_image() -> Refusal:
    """AWS would not say where its own images live, so nothing can be started.

    A refusal rather than a guessed account. A wrong account segment produces a
    ``ValidationException`` from ``create-app`` naming an ARN nobody wrote, which is a worse
    thing to put in front of somebody than a sentence saying the lookup failed.
    """
    return Refusal(
        code="image_account_unreadable",
        detail=(
            f"{IMAGE_ACCOUNT_PARAMETER} could not be read, and it is what says which account "
            "Amazon publishes its Studio images in. Nothing was started and nothing is "
            "billing. This is a call that failed rather than anything about what you typed, "
            "so running it again is the remedy."
        ),
    )


def unpriced_shape(settings: StudioSettings, instance_type: str) -> Refusal:
    """A shape nobody has costed, refused with the list of the ones somebody has."""
    priced = ", ".join(shape.instance_type for shape in settings.shapes)
    return Refusal(
        code="shape_is_not_priced",
        detail=(
            f"{instance_type} carries no rate in {STUDIO_CONFIG_FILE}, so this cannot "
            f"say what an hour of it costs and will not start it. Priced today: {priced}. "
            "Adding one is an edit to that file and a pull request."
        ),
    )


def studio_tags(request: StudioRequest) -> dict[str, str]:
    """The three tags every space and every app this verb creates carries.

    **``Project`` IS CAPITALISED AND THE OTHER TWO ARE PREFIXED, WHICH IS NOT INCONSISTENCY.**
    ``Project`` is the key ``infra/iam/researcher-role.yaml`` conditions on and the janitor
    filters on, spelled by :mod:`edullm_platform.researcher_lane` and imported here rather
    than typed, so a lane machine and a Studio app land in one Cost Explorer group instead of
    two that differ by a capital letter. The other two are this platform's own and carry the
    prefix that keeps them out of the role's tag-stripping deny.

    **A TAG THAT IS NOT ACTIVATED IS DECORATION AND THIS FUNCTION CANNOT FIX THAT.** Cost
    allocation tags are activated in the organisation's payer account, not here --
    ``ce:ListCostAllocationTags`` answers ``Linked account doesn't have access to cost
    allocation tags`` for this account, so the activation cannot even be read from inside it,
    let alone performed. Tagging on create is still the half worth doing first: an untagged
    app is unattributable for ever, where an untagged-but-activated key is attributable from
    the day somebody flips it.
    """
    return {
        PROJECT_TAG_KEY: request.project,
        PERSON_TAG_KEY: request.person,
        SURFACE_TAG_KEY: SURFACE_TAG_VALUE,
    }


def _tag_arguments(tags: Mapping[str, str]) -> list[str]:
    """Tags in the shape the SageMaker CLI takes, which is not the shape EC2 takes.

    ``Key=...,Value=...`` per tag against EC2's single ``ResourceType=...,Tags=[{...}]``
    blob. Sorted, so two invocations produce one argv and a test can compare them.
    """
    return ["--tags", *(f"Key={key},Value={value}" for key, value in sorted(tags.items()))]


def describe_user_profile_argv(
    *, settings: StudioSettings, request: StudioRequest
) -> tuple[str, ...]:
    """Whether this person has a user profile yet."""
    return (
        "aws",
        "sagemaker",
        "describe-user-profile",
        "--domain-id",
        settings.domain_id,
        "--user-profile-name",
        request.studio_name,
        "--output",
        "json",
    )


def create_user_profile_argv(
    *, settings: StudioSettings, request: StudioRequest
) -> tuple[str, ...]:
    """Make this person a user profile, so nobody is set up by hand.

    No ``--user-settings``. The domain's ``DefaultUserSettings`` already names the execution
    role, the landing URI and the web portal, and a profile that restated them would be a
    second copy of the domain's configuration that stops matching the day the domain moves.
    """
    return (
        "aws",
        "sagemaker",
        "create-user-profile",
        "--domain-id",
        settings.domain_id,
        "--user-profile-name",
        request.studio_name,
        *_tag_arguments(studio_tags(request)),
        "--output",
        "json",
    )


def describe_space_argv(*, settings: StudioSettings, request: StudioRequest) -> tuple[str, ...]:
    """Whether this person has a space yet, and how big its volume is."""
    return (
        "aws",
        "sagemaker",
        "describe-space",
        "--domain-id",
        settings.domain_id,
        "--space-name",
        request.studio_name,
        "--output",
        "json",
    )


def create_space_argv(
    *, settings: StudioSettings, request: StudioRequest, shape: StudioShape, image_arn: str
) -> tuple[str, ...]:
    """One private space owned by one user profile, which is Studio's own scoping.

    ``SharingType=Private`` with ``OwnerUserProfileName`` is the pair that makes a space one
    person's; either alone does not. The volume is sized here and never again -- Studio grows
    a space's EBS volume and does not shrink it -- and it is the charge that survives
    ``--stop``, which is why the verb says the number out loud rather than leaving it in a
    file.

    The space is named after the person rather than after the project. One space per person
    and not one per project, because the volume is the thing Studio is preferred for and a
    space per project is a disk per project, each billed monthly and each holding a slightly
    different half-configured environment.
    """
    return (
        "aws",
        "sagemaker",
        "create-space",
        "--domain-id",
        settings.domain_id,
        "--space-name",
        request.studio_name,
        "--ownership-settings",
        f"OwnerUserProfileName={request.studio_name}",
        "--space-sharing-settings",
        "SharingType=Private",
        "--space-settings",
        (
            f"AppType={APP_TYPE},"
            f"JupyterLabAppSettings={{DefaultResourceSpec={{InstanceType={shape.instance_type},"
            f"SageMakerImageArn={image_arn}}}}},"
            f"SpaceStorageSettings={{EbsStorageSettings={{EbsVolumeSizeInGb={settings.volume_gib}}}}}"
        ),
        *_tag_arguments(studio_tags(request)),
        "--output",
        "json",
    )


def describe_app_argv(*, settings: StudioSettings, request: StudioRequest) -> tuple[str, ...]:
    """What this person's app is doing, which is the question ``--stop`` and bare both ask."""
    return (
        "aws",
        "sagemaker",
        "describe-app",
        "--domain-id",
        settings.domain_id,
        "--space-name",
        request.studio_name,
        "--app-type",
        APP_TYPE,
        "--app-name",
        APP_NAME,
        "--output",
        "json",
    )


def create_app_argv(
    *, settings: StudioSettings, request: StudioRequest, shape: StudioShape, image_arn: str
) -> tuple[str, ...]:
    """Start the compute. This is the call that begins costing money.

    The image ARN is not optional and the service says so -- ``SageMaker Image ARN is required
    for App with type [JupyterLab]`` -- so it is passed in rather than defaulted.

    Tagged at creation and never afterwards, which is the half of the tagging story that
    matters: ``CreateApp`` is what CloudTrail records and what Cost Explorer bills, and a tag
    added by a later ``add-tags`` call does not retroactively attribute the hours before it.
    """
    return (
        "aws",
        "sagemaker",
        "create-app",
        "--domain-id",
        settings.domain_id,
        "--space-name",
        request.studio_name,
        "--app-type",
        APP_TYPE,
        "--app-name",
        APP_NAME,
        "--resource-spec",
        f"InstanceType={shape.instance_type},SageMakerImageArn={image_arn}",
        *_tag_arguments(studio_tags(request)),
        "--output",
        "json",
    )


def delete_app_argv(*, settings: StudioSettings, request: StudioRequest) -> tuple[str, ...]:
    """Stop the compute and keep the files.

    **``delete-app`` IS HOW STUDIO SPELLS "STOP" AND THE NAME IS ALARMING FOR NOTHING.** The
    EBS volume belongs to the *space*, not to the app, so deleting the app releases the
    instance and leaves every file where it was; the next ``edullm studio`` creates a new app
    against the same volume and the person finds their work. The account already demonstrates
    both halves: the ``test`` space has no app and still carries its volume.
    """
    return (
        "aws",
        "sagemaker",
        "delete-app",
        "--domain-id",
        settings.domain_id,
        "--space-name",
        request.studio_name,
        "--app-type",
        APP_TYPE,
        "--app-name",
        APP_NAME,
    )


def landing_uri(request: StudioRequest) -> str:
    """The deep link that puts somebody in their own space rather than on Studio's home page.

    **MEASURED AGAINST THE SERVICE AND NOT READ OFF A GUESS.** ``app:JupyterLab:<space>`` is
    the form the documentation's own list suggests and the API refuses it -- ``Provided app
    type JupyterLab is invalid for provided url type app for personal apps``. The
    ``studio::`` form is accepted and the issued token carries ``landingUriScheme: studio``
    with ``landingUriDeepLink: /jupyterlab/<space>``, which is how it was confirmed to have
    been understood rather than merely tolerated.
    """
    return f"studio::/jupyterlab/{request.studio_name}"


def presigned_url_argv(*, settings: StudioSettings, request: StudioRequest) -> tuple[str, ...]:
    """The URL somebody clicks, which signs them in with no console navigation at all.

    ``CreatePresignedDomainUrl`` works only where the domain's ``AuthMode`` is ``IAM``, which
    this one's is. The permissions the session lands with are the caller's, and the
    domain's ``ExecutionRoleSessionNameMode`` of ``USER_IDENTITY`` is what puts the person's
    own session name on what the notebook then does -- so a Studio action is attributable to a
    person in CloudTrail the same way a lane action is.
    """
    return (
        "aws",
        "sagemaker",
        "create-presigned-domain-url",
        "--domain-id",
        settings.domain_id,
        "--user-profile-name",
        request.studio_name,
        "--landing-uri",
        landing_uri(request),
        "--expires-in-seconds",
        str(PRESIGNED_URL_SECONDS),
        "--query",
        "AuthorizedUrl",
        "--output",
        "text",
    )


@dataclass(frozen=True)
class RunningApp:
    """An app that already exists, and what it is costing while it does."""

    status: str
    instance_type: str | None

    @property
    def is_billing(self) -> bool:
        """Whether this app is on the meter.

        ``Pending`` counts. An app coming up is an instance already allocated, and a verb
        that reported it as not costing anything would be wrong in the direction that starts
        a second one.
        """
        return self.status in ("InService", "Pending")


def running_app(described: str) -> RunningApp | None:
    """This person's app as ``describe-app`` answered, or nothing where there is none.

    ``None`` for an empty body, which is what the runner hands back when the call failed --
    and a call that failed because the app does not exist is the ordinary first invocation.
    Distinguishing "absent" from "unreadable" is not possible from the body alone and is not
    attempted here: the caller knows whether the command exited zero.

    A ``Deleted`` or ``Failed`` app is answered as itself rather than as nothing, because
    Studio leaves those records behind and ``create-app`` against one is what the verb has to
    decide about.
    """
    try:
        body = json.loads(described or "null")
    except ValueError:
        return None
    if not isinstance(body, Mapping):
        return None
    status = body.get("Status")
    if not isinstance(status, str):
        return None
    specification = body.get("ResourceSpec")
    instance_type = None
    if isinstance(specification, Mapping):
        named = specification.get("InstanceType")
        instance_type = named if isinstance(named, str) else None
    return RunningApp(status=status, instance_type=instance_type)


def price_said(shape: StudioShape, settings: StudioSettings) -> str:
    """What an hour costs, said before anything is started, the way ``check`` prices a run.

    Two numbers and not one, because they stop at different times and conflating them is the
    misunderstanding this verb exists to prevent. The instance rate stops when ``--stop``
    stops the app. The volume rate does not stop at all: it is the persistent disk that is the
    reason to prefer Studio, and it is billed whether or not anybody is signed in.

    The volume figure is a month because a gigabyte-month is the unit that charge accrues in.
    The instance figure is an hour and is deliberately not multiplied out into a day or a
    month -- a projection is a prediction about somebody's behaviour, and the honest thing to
    quote before they start an app is the rate.
    """
    monthly_volume = settings.volume_gib_month_usd * settings.volume_gib
    return (
        f"{shape.instance_type} is ${shape.hourly_rate_usd} an hour at list price, and the "
        f"{settings.volume_gib} GB volume is about ${monthly_volume:.2f} a month whether or "
        "not the app is running. The hourly charge stops when you run edullm studio --stop. "
        "The volume charge does not stop."
    )


def unstopped_said() -> str:
    """What happens to an app nobody stops, which is nothing, which is the problem.

    **THIS IS A MEASUREMENT AND NOT A WARNING WRITTEN TO BE SAFE.** The domain carries no
    ``AppLifecycleManagement.IdleSettings`` on its default user settings, none on the user
    profile, none on either space, and the account holds no Studio lifecycle configuration at
    all. The consequence was already paid for before this verb existed: one JupyterLab app on
    ``ml.g4dn.xlarge`` ran unattended from 2026-08-03 to 2026-08-06, across three nights,
    billing every hour of it. Until idle shutdown is turned on at the domain, ``--stop`` is
    the only thing that stops an app, and this sentence is the verb saying so rather than
    assuming somebody read the file.
    """
    return (
        "Nothing will stop this for you. The domain has no idle-shutdown setting, so an app "
        "left running overnight bills every hour until somebody stops it. Run edullm studio "
        "--stop when you are done."
    )


def already_running_said(app: RunningApp, *, url: str) -> str:
    """A second invocation, answered with the link rather than with a second app.

    Starting another would be the expensive reading of "start or resume": Studio permits more
    than one app per space, so the mistake is available and it is silent.
    """
    shape = app.instance_type or "a shape it did not report"
    return f"Your space is already running on {shape}, so this started nothing.\n\n{url}"


def nothing_to_stop(request: StudioRequest) -> Refusal:
    """``--stop`` with no app, which is not an error and is worth saying plainly."""
    return Refusal(
        code="nothing_to_stop",
        detail=(
            f"the space {request.studio_name} has no running app, so there is no compute to "
            "stop and nothing is being billed by the hour. Its volume is still there and so "
            "are your files."
        ),
    )


def studio_document(
    *,
    request: StudioRequest,
    settings: StudioSettings,
    shape: StudioShape | None,
    app: RunningApp | None,
) -> dict[str, Any]:
    """What this verb established, for a caller that is a program rather than a person.

    The same envelope discipline ``cli/machine.py`` applies: a caller branches on the fields
    and prints ``said``, and never parses the paragraphs. ``url`` is deliberately absent --
    a presigned URL is a bearer credential with a short life, and putting one in a document
    somebody redirects to a file is how it ends up in a repository.
    """
    return {
        "domain_id": settings.domain_id,
        "space": request.studio_name,
        "person": request.person,
        "project": request.project,
        "instance_type": None if shape is None else shape.instance_type,
        "hourly_rate_usd": None if shape is None else str(shape.hourly_rate_usd),
        "volume_gib": settings.volume_gib,
        "app_status": None if app is None else app.status,
        "idle_shutdown": False,
    }
