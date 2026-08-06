"""What ``edullm studio`` decides without an account, which is nearly all of it.

THIS MODULE MAKES NO AWS CALL AND RUNS NO PROCESS, which is ``cli/lane.py``'s arrangement and
is here for the same reason: it answers what a person's spaces are called, what a shape costs,
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

**``--project`` NAMES THE SPACE, AND ONE PERSON MAY HAVE AS MANY AS THEY HAVE PROJECTS.** This
was one space per person until 2026-08-06 and that was the wrong shape. A space carries its own
disk, so two projects that want different dependencies, different data and different half-built
state want two spaces; forcing them into one makes the disk a shared mutable thing and the
person the merge conflict. Since ``--project`` is already required to start anything and already
tags everything, letting it name the space makes the tag and the space agree by construction
rather than by a convention somebody has to remember -- and it removes the resume-or-start
question entirely, because a project you have used before is a space that exists and a project
you have not is a space that does not.

**A SPACE IS FOUND BY OWNERSHIP AND ADDRESSED BY NAME, AND THE TWO JOBS ARE NOT THE SAME JOB.**
:func:`space_name_for` derives an address, because a person typing ``--project lab`` has to
reach the same disk every time and an address that is computed is one nobody has to record.
:func:`owned_spaces` then reads ``OwnershipSettingsSummary.OwnerUserProfileName`` off what
``ListSpaces`` actually returned, and nothing is resumed, started or stopped until that field
says the caller owns it. So the name is a guess about where to look and the ownership is the
fact that settles it: a derived name that happens to land on somebody else's space is refused
rather than opened, and a space named by hand off the convention is still listed by
``edullm studio`` even though ``--project`` cannot address it. Deriving alone would be the
convention-baked-in failure the owner named; ownership alone cannot answer "which of my six".

**PROFILES AND SPACES SHARE ONE NAMESPACE IN A DOMAIN AND THIS IS WHY THE NAME HAS TWO PARTS.**
``CreateSpace`` answers ``User Profile already exists with the same name`` for a space named
after a profile, which is what a space-per-person arrangement produces on its first invocation
for every person alive. ``<person>-<project>`` cannot collide with the caller's own profile,
because a project is never empty. It can still collide with *somebody else's* profile, where
that person's normalised name happens to be this person's name plus a dash plus this project --
a hyphenated surname makes it possible rather than impossible -- so it is detected before the
create rather than reasoned away, and :func:`project_collides_with_a_profile` is what a person
gets instead of a raw ``ResourceInUse``.

**NOTHING HERE WRITES AN ``ExpiresAt`` TAG AND THE OMISSION IS DELIBERATE.**
``infra/expiry-janitor.yaml`` sweeps EC2 instances by that tag and has no SageMaker arm, so an
``ExpiresAt`` on a Studio app would be a promise nothing in this repository keeps -- worse than
no tag, because the next reader would find it and conclude a machine was being watched.

**AND NOTHING HERE LIMITS HOW MANY SPACES A PERSON MAY HAVE, WHICH IS A RULING AND NOT AN
OVERSIGHT.** Spaces accumulate, each carries a disk, and no sweep reclaims one -- the janitor
has no SageMaker arm, as above. A ceiling was the obvious answer and it is the wrong one on
every count. It refuses at the moment somebody starts work; it refuses the cheap thing, a new
five-gigabyte disk at about half a dollar a month, in order to punish the disks they already
have; it reclaims nothing; and the number would be undefendable, because nobody can say why six
and not nine. The quantities are two orders of magnitude apart: the unattended
``ml.g4dn.xlarge`` app that ran from 2026-08-03 to 2026-08-06 cost more than sixty space-months
of disk. So the verb makes the accumulation *visible* instead -- :func:`accumulation_said` puts
the count and the monthly total in front of somebody every time they add one, and
:func:`spaces_said` lists what they have with what each costs. Visibility is what makes a person
delete their own and what makes a sweep buildable later. The sweep is the honest missing piece
and it is recorded here as missing rather than papered over with a limit.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
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
    "IdleShutdown",
    "OwnedSpace",
    "RunningApp",
    "SpaceRecord",
    "StudioRequest",
    "StudioSettings",
    "StudioShape",
    "accumulation_said",
    "already_running_said",
    "apps_by_space",
    "could_not_resolve_the_image",
    "create_app_argv",
    "create_space_argv",
    "create_user_profile_argv",
    "delete_app_argv",
    "describe_app_argv",
    "describe_domain_argv",
    "describe_user_profile_argv",
    "idle_said",
    "idle_shutdown",
    "image_account_argv",
    "image_arn_for",
    "list_apps_argv",
    "list_spaces_argv",
    "load_studio_settings",
    "monthly_volume_cost",
    "no_spaces_said",
    "nothing_to_stop",
    "owned_spaces",
    "portal_uri",
    "presigned_url_argv",
    "price_said",
    "project_collides_with_a_profile",
    "project_of_space",
    "running_app",
    "shape_for",
    "space_belongs_to_somebody_else",
    "space_name_for",
    "space_named",
    "spaces_said",
    "starting_said",
    "studio_document",
    "studio_name_for",
    "studio_refusals",
    "studio_tags",
    "unpriced_shape",
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
#: surface that the argument for preferring Studio rests on, and it is what all twenty spaces
#: in the domain already run.
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
#: ``tests/test_cli_studio.py`` holds the roster against it.
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

#: How long a presigned URL is good for, and **it is the service's ceiling rather than a choice**.
#:
#: ``ExpiresInSeconds`` is documented as "Minimum value of 5. Maximum value of 300", and the live
#: API agrees to the second: 300 is issued and 301 is a ``ValidationException``. So there is no
#: number to raise this to, and the failure that made everybody want to -- "you are directed to
#: the Amazon Web Services console sign-in page", which is what AWS says an expired URL does and
#: exactly what people reported -- cannot be fixed by asking for longer.
#:
#: **IT IS FIXED BY SPENDING THE FIVE MINUTES ON A BROWSER RATHER THAN ON A PERSON.**
#: ``cli/browser.py`` opens the URL where it was minted, so the elapsed time between cutting a
#: credential and redeeming it is a process start rather than somebody noticing their terminal,
#: reading four thousand characters out of it and getting them into a browser intact. The five
#: minutes was never generous for the second thing and is enormous for the first.
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

    Four fields and two are derived. ``person`` is what the caller ARN says and ``studio_name``
    is that same person spelled the way SageMaker will accept; ``space`` is that name joined to
    the project. All three are held rather than recomputed, because they appear in different
    places -- the person in a tag and in the working tier, the Studio name in a user profile and
    a presigned URL, the space in a create, a describe and a deep link -- and a second
    derivation is a second chance for them to disagree.

    An empty ``project`` is the bare invocation, which lists rather than starting anything, so
    an empty ``space`` follows from it and is not a failure.
    """

    person: str
    studio_name: str
    project: str
    space: str


def studio_name_for(person: str) -> str:
    """One person's name as SageMaker will take it, or the empty string where it cannot.

    Empty rather than a raise, because the caller has a refusal to render and a traceback in
    front of a researcher is the one thing this binary promises not to produce.
    """
    collapsed = _UNSAFE_IN_A_STUDIO_NAME.sub("-", person)
    trimmed = _EDGE_DASHES.sub("", collapsed)[:STUDIO_NAME_LIMIT]
    return _EDGE_DASHES.sub("", trimmed)


def space_name_for(studio_name: str, project: str) -> str:
    """The space one person's project lives in, or the empty string where it will not fit.

    **THE RULE IS ``<person>-<project>`` AND IT IS NOT A CONVENTION THIS INVENTED TODAY.** The
    twenty spaces in the domain are ``<person>-lab`` owned by ``<person>``, so ``--project lab``
    resolves to the space that is already there and creates nothing -- which falls out of the
    rule rather than out of a special case for the word ``lab``, and would fall out the same way
    if somebody had named them ``-scratch``.

    Both halves go through :func:`studio_name_for`, so the join of two legal names is a legal
    name: neither half can open or close on a dash, so the single dash between them cannot
    become the doubled dash or the leading dash SageMaker refuses.

    Empty where the person is empty, where the project has nothing SageMaker will take in it, or
    where the two together exceed :data:`STUDIO_NAME_LIMIT`. **THE LENGTH IS NOT TRUNCATED AND
    THAT IS THE WHOLE POINT.** Cutting it would silently point two long project names at one
    disk, which is two people's work in one place with nothing saying so; the refusal
    :func:`studio_refusals` renders names the budget instead.
    """
    if not studio_name:
        return ""
    segment = studio_name_for(project)
    if not segment:
        return ""
    joined = f"{studio_name}-{segment}"
    return "" if len(joined) > STUDIO_NAME_LIMIT else joined


def project_of_space(studio_name: str, space: str) -> str | None:
    """Which ``--project`` reaches this space, or nothing where no spelling of one would.

    The inverse of :func:`space_name_for`, and it is what lets :func:`spaces_said` tell somebody
    the word to type rather than the space to remember. ``None`` for a space of theirs that does
    not carry the prefix -- one made by hand under another name -- which is listed as itself and
    marked as unreachable by ``--project``, because a listing that quietly dropped it would be a
    disk billing to somebody who has been told they do not have it.
    """
    prefix = f"{studio_name}-"
    if not studio_name or not space.startswith(prefix):
        return None
    remainder = space[len(prefix) :]
    return remainder or None


def studio_refusals(request: StudioRequest) -> tuple[Refusal, ...]:
    """Everything this verb refuses about who and what, which is the whole list.

    **NONE OF THEM IS A PERMISSION, WHICH IS THE TEST ``cli/lane.py`` SETS AND THIS INHERITS.**
    One says the caller cannot be named and the others say a destination cannot be spelled.
    Studio is the exploration surface: nothing here is checked against the registry, priced
    against a policy, approved or written to a lineage record, and a refusal that withheld a
    shape from a person would be the submission path arriving by the back door.

    **``no_project`` IS GONE FROM THIS LIST AND ITS ARGUMENT IS NOT.** It fired on a bare
    ``edullm studio``, and that was right while a person had one space and wrong the moment
    ``--project`` began naming which of several. The reason it gave -- that no default is
    possible, because a default puts two unrelated pieces of work under one name and one bill --
    is still the reason there is no default, and it is now in ``--project``'s help and in
    :func:`no_spaces_said`, where it reaches the person who needs it. What changed is only that
    a bare invocation lists what somebody has instead of refusing to guess, and refusing to
    guess was never the same act as refusing to answer.
    """
    refusals: list[Refusal] = []
    if not request.person:
        refusals.append(
            Refusal(
                code="cannot_tell_who_you_are",
                detail=(
                    "this session is already inside the lane, and sts:GetCallerIdentity does "
                    "not return the source identity, so which person's spaces to open cannot "
                    "be read from it. Run this from your ordinary session."
                ),
            )
        )
        return tuple(refusals)
    if not request.studio_name:
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
        return tuple(refusals)
    if request.project and not request.space:
        refusals.append(_unusable_project(request))
    return tuple(refusals)


def _unusable_project(request: StudioRequest) -> Refusal:
    """A project that cannot become a space name, said with the budget it overran.

    Two causes and one refusal, because the remedy is the same sentence either way: pick a
    shorter or a plainer name. The budget is computed rather than quoted, because it is the
    caller's own name that spends most of it and a fixed number would be wrong for everybody
    whose name is not the length of the example.
    """
    budget = STUDIO_NAME_LIMIT - len(request.studio_name) - 1
    if not studio_name_for(request.project):
        return Refusal(
            code="project_name_is_unusable",
            detail=(
                f"--project {request.project!r} has nothing left in it once the characters "
                "SageMaker refuses in a space name are removed, so there is no space it could "
                "name. The service takes letters and digits, and this turns anything else into "
                "a dash."
            ),
        )
    return Refusal(
        code="project_name_is_too_long",
        detail=(
            f"--project {request.project!r} makes a space name longer than the "
            f"{STUDIO_NAME_LIMIT} characters SageMaker allows. Your name takes "
            f"{len(request.studio_name)} of them, so a project has {budget} to spend. This is "
            "not truncated on purpose: two long names cut to one would put two pieces of work "
            "on one disk with nothing saying so."
        ),
    )


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
    which needs no permission beyond the lane's. It is made once per start and never on a
    listing or a ``--stop``, neither of which reaches an image at all.
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


def space_belongs_to_somebody_else(request: StudioRequest, *, owner: str) -> Refusal:
    """The derived name landed on a space that is not the caller's, so nothing was opened.

    **THIS IS THE REFUSAL THAT MAKES DERIVING A NAME SAFE.** A name is a guess about where to
    look and this is what happens when the guess is wrong: the ownership field settles it and
    the verb stops. Without it the same code path would resume somebody else's disk, and Studio
    would let it, because the domain's execution role is shared and a private space is private
    by convention in the console rather than by an authorisation on this call.
    """
    return Refusal(
        code="space_belongs_to_somebody_else",
        detail=(
            f"the space {request.space} already exists and {owner} owns it, so nothing was "
            f"opened. --project {request.project!r} makes that name from yours, and it has "
            "collided with a space somebody else made. Pick another project name; nothing of "
            "theirs and nothing of yours was touched."
        ),
    )


def project_collides_with_a_profile(request: StudioRequest) -> Refusal:
    """The space this project would need is already a user profile's name.

    **PROFILES AND SPACES SHARE ONE NAMESPACE, WHICH IS THE DEFECT THAT PRODUCED THIS FUNCTION.**
    ``CreateSpace`` answers ``ResourceInUse`` with a sentence about user profiles, which is a
    true thing to say and an incomprehensible one to receive when you typed a project name. The
    collision needs a hyphenated surname to happen at all, so it is rare rather than impossible,
    and rare failures are exactly the ones worth spelling out: the person who hits this will
    hit it once and have no idea why.
    """
    return Refusal(
        code="project_collides_with_a_profile",
        detail=(
            f"--project {request.project!r} would need a space called {request.space}, and "
            "that is already the name of somebody's user profile. SageMaker keeps profiles "
            "and spaces in one namespace, so that name cannot be a space. Nothing was created "
            "and nothing is billing. Pick another project name."
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


def describe_domain_argv(settings: StudioSettings) -> tuple[str, ...]:
    """The domain, which is where the idle-shutdown setting is and where it changes.

    **READ RATHER THAN WRITTEN DOWN, BECAUSE THE WRITTEN VERSION WENT STALE IN AN HOUR.** This
    verb shipped saying the domain had no idle shutdown, which was measured and true; the domain
    was given a 240-minute timeout the same afternoon and the sentence became a lie that nobody
    would have noticed until somebody left a GPU on believing it. A number in the output that
    came from the API cannot go stale, and a number in a file always eventually can.
    """
    return (
        "aws",
        "sagemaker",
        "describe-domain",
        "--domain-id",
        settings.domain_id,
        "--output",
        "json",
    )


def list_spaces_argv(settings: StudioSettings) -> tuple[str, ...]:
    """Every space in the domain, which is one call and answers three questions.

    **NOT FILTERED, BECAUSE ``ListSpaces`` HAS NO OWNER FILTER AND BECAUSE THE UNFILTERED ANSWER
    IS THE MORE USEFUL ONE.** The summary carries ``OwnershipSettingsSummary`` and
    ``SpaceSettingsSummary`` for every space, so this single call says which spaces are the
    caller's, how big each of their disks is, and -- for the space ``--project`` derived --
    whether it exists and whether somebody else owns it. Three describes would answer less.

    No ``--max-results``. The AWS CLI follows ``NextToken`` by itself unless a page size is
    named, so passing one is how a domain silently starts reporting the first hundred spaces.
    """
    return (
        "aws",
        "sagemaker",
        "list-spaces",
        "--domain-id-equals",
        settings.domain_id,
        "--output",
        "json",
    )


def list_apps_argv(settings: StudioSettings) -> tuple[str, ...]:
    """Every app in the domain, which is what says which of somebody's spaces are billing.

    One call rather than a ``describe-app`` per space, which is what a person with six spaces
    would otherwise pay for on every listing. It is also what ``--stop`` with no project needs:
    the set of this person's apps that are actually on the meter.
    """
    return (
        "aws",
        "sagemaker",
        "list-apps",
        "--domain-id-equals",
        settings.domain_id,
        "--output",
        "json",
    )


def describe_user_profile_argv(*, settings: StudioSettings, name: str) -> tuple[str, ...]:
    """Whether a user profile of this name exists.

    Asked about two different names by two different callers, which is why it takes one rather
    than reading it off the request. The caller's own profile is asked about to create it where
    they are new; the *space* name is asked about to catch the namespace collision that
    :func:`project_collides_with_a_profile` explains, before a create returns something nobody
    can read.
    """
    return (
        "aws",
        "sagemaker",
        "describe-user-profile",
        "--domain-id",
        settings.domain_id,
        "--user-profile-name",
        name,
        "--output",
        "json",
    )


def create_user_profile_argv(
    *, settings: StudioSettings, request: StudioRequest
) -> tuple[str, ...]:
    """Make this person a user profile, so nobody is set up by hand.

    No ``--user-settings``. The domain's ``DefaultUserSettings`` already names the execution
    role, the landing URI, the web portal and the idle-shutdown timeout, and a profile that
    restated them would be a second copy of the domain's configuration that stops matching the
    day the domain moves -- which it did, on the afternoon this was written.
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


def create_space_argv(
    *, settings: StudioSettings, request: StudioRequest, shape: StudioShape, image_arn: str
) -> tuple[str, ...]:
    """One private space owned by one user profile, which is Studio's own scoping.

    ``SharingType=Private`` with ``OwnerUserProfileName`` is the pair that makes a space one
    person's; either alone does not. It is also the pair :func:`owned_spaces` reads back, so
    what this writes is what discovery later depends on.

    The volume is sized here and never again -- Studio grows a space's EBS volume and does not
    shrink it -- and it is the charge that survives ``--stop``, which is why the verb says the
    number out loud rather than leaving it in a file.
    """
    return (
        "aws",
        "sagemaker",
        "create-space",
        "--domain-id",
        settings.domain_id,
        "--space-name",
        request.space,
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


def describe_app_argv(*, settings: StudioSettings, space: str) -> tuple[str, ...]:
    """What one space's app is doing, which is the question a start asks about its own space."""
    return (
        "aws",
        "sagemaker",
        "describe-app",
        "--domain-id",
        settings.domain_id,
        "--space-name",
        space,
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
        request.space,
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


def delete_app_argv(*, settings: StudioSettings, space: str) -> tuple[str, ...]:
    """Stop the compute and keep the files.

    **``delete-app`` IS HOW STUDIO SPELLS "STOP" AND THE NAME IS ALARMING FOR NOTHING.** The
    EBS volume belongs to the *space*, not to the app, so deleting the app releases the
    instance and leaves every file where it was; the next ``edullm studio --project <same>``
    creates a new app against the same volume and the person finds their work. Every one of
    the twenty spaces in the domain demonstrates the resting state: a volume, and no app.

    It takes a space rather than a request, because ``--stop`` with no project stops each of
    several and there is one of these per app rather than one per invocation.
    """
    return (
        "aws",
        "sagemaker",
        "delete-app",
        "--domain-id",
        settings.domain_id,
        "--space-name",
        space,
        "--app-type",
        APP_TYPE,
        "--app-name",
        APP_NAME,
    )


def portal_uri(request: StudioRequest) -> str:
    """The Studio portal page for one space, which is where somebody goes when it is not up.

    **THE PATH IS RELATIVE AND THE LEADING SLASH THIS USED TO CARRY WAS THE THIRD FAILURE.**
    ``CreatePresignedDomainUrl`` documents ``studio::relative/path``, and this passed
    ``studio::/jupyterlab/<space>``. The service accepted it, minted a token carrying
    ``landingUriDeepLink: /jupyterlab/<space>``, and then redeemed it into
    ``Location: //jupyterlab/<space>`` -- a protocol-relative URL, which every browser reads as
    the host ``jupyterlab``. Chrome resolves nothing, shows its own error page and titles it
    ``jupyterlab``. It was measured against ``d-bxqz8jfqjjnu`` on 2026-08-06 in a real browser,
    and it failed **identically on a space whose app was running**, which is what rules out the
    obvious hypothesis that the deep link was merely pointing at an app that did not exist yet.
    One character. Dropping it turns the same redirect into ``/jupyterlab/<space>`` and answers
    200 on the portal.

    The portal page is not the notebook: it is the page for that space, carrying its status, its
    shape, its disk and an **Open JupyterLab** button. That makes it the right destination for a
    space whose app is starting or absent, and the wrong one for a space that is up --
    :func:`presigned_url_argv` chooses between them.
    """
    return f"studio::jupyterlab/{request.space}"


def presigned_url_argv(
    *, settings: StudioSettings, request: StudioRequest, app_is_running: bool
) -> tuple[str, ...]:
    """The URL somebody is taken to, aimed at a destination that is valid at the moment it is cut.

    ``CreatePresignedDomainUrl`` works only where the domain's ``AuthMode`` is ``IAM``, which
    this one's is. The permissions the session lands with are the caller's, and the domain's
    ``ExecutionRoleSessionNameMode`` of ``USER_IDENTITY`` is what puts the person's own session
    name on what the notebook then does -- so a Studio action is attributable to a person in
    CloudTrail the same way a lane action is.

    **``--space-name`` IS AWS'S OWN ANSWER TO "REACH A SPACE" AND THIS VERB WAS NOT USING IT.**
    The *Launch spaces* page gives exactly one CLI recipe for an IAM domain and it is
    ``create-presigned-domain-url --domain-id ... --user-profile-name ... --space-name ...``.
    Measured, it redirects to the space's own host and lands inside JupyterLab at
    ``/jupyterlab/default/lab`` with the person's files already open. No landing URI reaches
    that host, because no landing URI is resolved against a space.

    **AND IT 404s WHERE NO APP IS RUNNING, WHICH IS WHY THIS TAKES A BOOLEAN.** A space with no
    app has nothing serving ``/jupyterlab/default``, so the same URL that is perfect one minute
    after a start is a blank 404 one minute before it. The alternative was to start the app and
    wait for ``InService`` before minting -- and that cannot be made safe, because
    :data:`PRESIGNED_URL_SECONDS` is a **hard** ceiling of 300 that the API refuses 301 against,
    and a JupyterLab app does not reliably come up inside five minutes. Choosing the destination
    instead means the URL is only ever cut for somewhere that exists right now, and the five
    minutes has to cover opening a browser rather than starting a machine.
    """
    destination = (
        ("--space-name", request.space)
        if app_is_running
        else ("--landing-uri", portal_uri(request))
    )
    return (
        "aws",
        "sagemaker",
        "create-presigned-domain-url",
        "--domain-id",
        settings.domain_id,
        "--user-profile-name",
        request.studio_name,
        *destination,
        "--expires-in-seconds",
        str(PRESIGNED_URL_SECONDS),
        "--query",
        "AuthorizedUrl",
        "--output",
        "text",
    )


@dataclass(frozen=True)
class SpaceRecord:
    """One space as ``ListSpaces`` reported it, whoever owns it."""

    name: str
    owner: str
    volume_gib: int | None
    status: str


@dataclass(frozen=True)
class OwnedSpace:
    """One of the caller's own spaces, with the word that reaches it.

    ``project`` is ``None`` for a space of theirs whose name does not carry their prefix, which
    is a space made by hand rather than by this verb. It is listed anyway: it is a disk billing
    to them, and a listing that showed only what this tool made would be a listing that hides
    exactly the spend nobody is watching.
    """

    name: str
    project: str | None
    volume_gib: int | None
    status: str


def _spaces(listed: str) -> tuple[SpaceRecord, ...]:
    """``ListSpaces`` parsed, skipping anything that does not carry a name and an owner.

    A space with no ``OwnershipSettingsSummary`` is a shared space nobody owns, which the
    console can make and this verb cannot; skipping it is right, because every question here is
    about whose it is and the answer for that one is nobody's.
    """
    try:
        body = json.loads(listed or "null")
    except ValueError:
        return ()
    if not isinstance(body, Mapping):
        return ()
    entries = body.get("Spaces")
    if not isinstance(entries, Sequence) or isinstance(entries, str):
        return ()
    found: list[SpaceRecord] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        name = entry.get("SpaceName")
        ownership = entry.get("OwnershipSettingsSummary")
        owner = ownership.get("OwnerUserProfileName") if isinstance(ownership, Mapping) else None
        if not isinstance(name, str) or not isinstance(owner, str):
            continue
        status = entry.get("Status")
        found.append(
            SpaceRecord(
                name=name,
                owner=owner,
                volume_gib=_volume_of(entry.get("SpaceSettingsSummary")),
                status=status if isinstance(status, str) else "",
            )
        )
    return tuple(found)


def _volume_of(summary: Any) -> int | None:
    """A space's disk size out of the settings summary, or nothing where it did not say.

    ``None`` rather than the configured default, because the configured default is what a space
    this verb creates gets and says nothing about a space somebody else made -- the twenty in
    the domain carry five gigabytes against a configured fifty. Quoting the wrong one would be a
    cost figure that is wrong by ten times in the reassuring direction.
    """
    if not isinstance(summary, Mapping):
        return None
    storage = summary.get("SpaceStorageSettings")
    if not isinstance(storage, Mapping):
        return None
    ebs = storage.get("EbsStorageSettings")
    if not isinstance(ebs, Mapping):
        return None
    size = ebs.get("EbsVolumeSizeInGb")
    return size if isinstance(size, int) else None


def owned_spaces(listed: str, *, owner: str) -> tuple[OwnedSpace, ...]:
    """Every space this person owns, whatever anybody named it, sorted by name.

    **THIS IS THE DISCOVERY AND IT READS THE SERVICE RATHER THAN A CONVENTION.** The filter is
    ``OwnershipSettingsSummary.OwnerUserProfileName``, which is the field Studio uses to mean
    exactly this, so a space made in the console an hour ago by somebody who named it whatever
    they liked is found by the same code that finds one this verb created.
    """
    return tuple(
        sorted(
            (
                OwnedSpace(
                    name=space.name,
                    project=project_of_space(owner, space.name),
                    volume_gib=space.volume_gib,
                    status=space.status,
                )
                for space in _spaces(listed)
                if space.owner == owner
            ),
            key=lambda space: space.name,
        )
    )


def space_named(listed: str, name: str) -> SpaceRecord | None:
    """The space of this exact name, whoever owns it, or nothing where there is none.

    Whoever owns it, because the caller of this needs to tell "yours, resume it" from "somebody
    else's, refuse" from "nobody's, create it", and a lookup that filtered on ownership first
    would flatten the middle case into the last one and try to create a space that exists.
    """
    return next((space for space in _spaces(listed) if space.name == name), None)


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
    """This space's app as ``describe-app`` answered, or nothing where there is none.

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


def apps_by_space(listed: str) -> dict[str, RunningApp]:
    """Which of the domain's spaces have an app on the meter, keyed by space.

    Only the billing ones are kept. ``ListApps`` reports every app that ever existed, including
    the ``Deleted`` records Studio leaves behind for ever, and a listing that counted those as
    running would tell everybody in the domain that they are paying for something they stopped
    last week.
    """
    try:
        body = json.loads(listed or "null")
    except ValueError:
        return {}
    if not isinstance(body, Mapping):
        return {}
    entries = body.get("Apps")
    if not isinstance(entries, Sequence) or isinstance(entries, str):
        return {}
    found: dict[str, RunningApp] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        space = entry.get("SpaceName")
        status = entry.get("Status")
        if not isinstance(space, str) or not isinstance(status, str):
            continue
        if entry.get("AppType") != APP_TYPE:
            continue
        specification = entry.get("ResourceSpec")
        instance_type = None
        if isinstance(specification, Mapping):
            named = specification.get("InstanceType")
            instance_type = named if isinstance(named, str) else None
        app = RunningApp(status=status, instance_type=instance_type)
        if app.is_billing:
            found[space] = app
    return found


@dataclass(frozen=True)
class IdleShutdown:
    """What the domain does to an app nobody stops.

    ``minutes`` is ``None`` where lifecycle management is off or the domain did not say, which
    is the state the account was in until 2026-08-06 and may be again.
    """

    minutes: int | None

    @property
    def enabled(self) -> bool:
        return self.minutes is not None


def idle_shutdown(described: str) -> IdleShutdown:
    """The domain's idle timeout, read off ``DescribeDomain``.

    **DISABLED IS THE ANSWER FOR ANYTHING THIS CANNOT READ, WHICH IS THE SAFE DIRECTION.** An
    unreadable domain that was reported as "Studio will stop this for you" is the sentence that
    leaves a GPU on all weekend; an idle timeout that exists and is reported as absent costs
    somebody one unnecessary ``--stop``. The asymmetry decides it.

    Read off ``DefaultUserSettings``, which is where the domain carries it. A per-space or
    per-profile override would win over this one and is not read: none of the twenty spaces has
    one, and the extra describe per space would be paid on every invocation to catch a case
    nobody has created. What the verb says is what the *domain* sets, and it says so in those
    words rather than claiming to know what will happen.
    """
    try:
        body = json.loads(described or "null")
    except ValueError:
        return IdleShutdown(minutes=None)
    settings: Any = body
    for key in ("DefaultUserSettings", "JupyterLabAppSettings", "AppLifecycleManagement"):
        if not isinstance(settings, Mapping):
            return IdleShutdown(minutes=None)
        settings = settings.get(key)
    if not isinstance(settings, Mapping):
        return IdleShutdown(minutes=None)
    idle = settings.get("IdleSettings")
    if not isinstance(idle, Mapping) or idle.get("LifecycleManagement") != "ENABLED":
        return IdleShutdown(minutes=None)
    minutes = idle.get("IdleTimeoutInMinutes")
    return IdleShutdown(minutes=minutes if isinstance(minutes, int) and minutes > 0 else None)


def monthly_volume_cost(spaces: Iterable[OwnedSpace], settings: StudioSettings) -> Decimal:
    """What this person's disks cost a month, whether or not anything is running.

    A space whose size did not come back is costed at the configured size rather than at
    nothing, because a total that silently omitted a disk would be the reassuring kind of wrong.
    """
    total = Decimal(0)
    for space in spaces:
        size = settings.volume_gib if space.volume_gib is None else space.volume_gib
        total += settings.volume_gib_month_usd * size
    return total


def price_said(shape: StudioShape, settings: StudioSettings, *, volume_gib: int) -> str:
    """What an hour costs, said before anything is started, the way ``check`` prices a run.

    Two numbers and not one, because they stop at different times and conflating them is the
    misunderstanding this verb exists to prevent. The instance rate stops when ``--stop``
    stops the app. The volume rate does not stop at all: it is the persistent disk that is the
    reason to prefer Studio, and it is billed whether or not anybody is signed in.

    The volume figure is a month because a gigabyte-month is the unit that charge accrues in.
    The instance figure is an hour and is deliberately not multiplied out into a day or a
    month -- a projection is a prediction about somebody's behaviour, and the honest thing to
    quote before they start an app is the rate.

    ``volume_gib`` is passed rather than read off the settings, because the settings say what a
    space this verb *creates* gets and the caller usually has one that already exists at some
    other size. The twenty spaces in the domain carry five gigabytes against a configured fifty.
    """
    monthly_volume = settings.volume_gib_month_usd * volume_gib
    return (
        f"{shape.instance_type} is ${shape.hourly_rate_usd} an hour at list price, and the "
        f"{volume_gib} GB volume is about ${monthly_volume:.2f} a month whether or "
        "not the app is running. The hourly charge stops when you stop the app. "
        "The volume charge does not stop."
    )


def idle_said(idle: IdleShutdown, shape: StudioShape) -> str:
    """What happens to an app nobody stops, according to the domain rather than to this file.

    **THE FIRST VERSION OF THIS SENTENCE WAS TRUE FOR ABOUT AN HOUR.** It said the domain had no
    idle shutdown, which was measured and correct on the morning of 2026-08-06 and false by that
    afternoon, when the domain was given a 240-minute timeout. The number now comes from
    :func:`idle_shutdown` reading ``DescribeDomain`` on the way past, so the only way for it to
    go stale is for the domain to change between the read and the print.

    ``--stop`` still matters and the reason is now smaller and worth stating in money: the
    timeout is what somebody pays before Studio acts, and stopping when they finish is what
    turns that into nothing.
    """
    if not idle.enabled or idle.minutes is None:
        return (
            "Nothing will stop this for you. The domain has no idle-shutdown setting today, so "
            "an app left running overnight bills every hour until somebody stops it. Stop it "
            "when you are done."
        )
    hours = Decimal(idle.minutes) / Decimal(60)
    wasted = shape.hourly_rate_usd * hours
    return (
        f"The domain stops an idle app by itself after {idle.minutes} minutes, so the most you "
        f"can leave on the meter by walking away is about ${wasted:.2f} of "
        f"{shape.instance_type}. Stopping it yourself when you finish costs nothing and saves "
        "that."
    )


def already_running_said(app: RunningApp, request: StudioRequest) -> str:
    """A second invocation, answered with the running app rather than with a second one.

    Starting another would be the expensive reading of "start or resume": Studio permits more
    than one app per space, so the mistake is available and it is silent.

    **IT NO LONGER CARRIES THE URL AND THAT IS THE POINT OF THE CHANGE AROUND IT.** This used to
    end with four thousand characters for somebody to select out of their scrollback. The URL now
    goes to a browser this process opens, so what is left to say is the thing the browser cannot:
    that nothing new was started and nothing new is billing.
    """
    shape = app.instance_type or "a shape it did not report"
    return (
        f"{request.space} is already running on {shape}, so this started nothing and "
        "nothing new is billing."
    )


def starting_said(request: StudioRequest) -> str:
    """What somebody is told when the app was not up and has just been asked to come up.

    **THEY ARE NOT LANDED IN A NOTEBOOK AND THE SENTENCE HAS TO SAY SO**, because the browser
    that just opened looks like a success and is showing a page about a space rather than the
    space. :func:`presigned_url_argv` records why the destination differs: a URL aimed at
    ``/jupyterlab/default`` on a space with no app is a blank 404, so the honest destination
    while an app starts is the page that shows it starting.
    """
    return (
        f"{request.space} had nothing running, so an app is starting now. The page that just "
        "opened is that space in Studio, and it shows the app coming up -- a few minutes, "
        "usually. Open JupyterLab from there when the status says Running. Running this verb "
        "again once it is up takes you straight into the notebook instead."
    )


def accumulation_said(spaces: Sequence[OwnedSpace], settings: StudioSettings) -> str:
    """How many disks this person now has and what they cost, said when one is added.

    **THIS IS WHAT THE VERB DOES INSTEAD OF A CEILING, AND THE MODULE DOCSTRING ARGUES WHY.**
    The number is put in front of somebody at the one moment it is their doing, which is the
    moment they can still pick a project name they already have. Nothing is refused.
    """
    total = monthly_volume_cost(spaces, settings)
    if len(spaces) == 1:
        return (
            f"This is your first space. Its disk costs about ${total:.2f} a month and keeps "
            "costing that until somebody deletes the space, which nothing here does for you."
        )
    return (
        f"You now have {len(spaces)} spaces, and their disks cost about ${total:.2f} a month "
        "between them whether or not anything is running. Nothing deletes a space for you. Run "
        "edullm studio with no --project to see them."
    )


def no_spaces_said() -> str:
    """What a bare invocation says to somebody who has none yet.

    **THE ARGUMENT AGAINST A DEFAULT PROJECT LIVES HERE NOW**, having previously been the
    ``no_project`` refusal. It is the same argument and it is made to the same person; what
    changed is that they get it while being told how to proceed rather than as the reason they
    were stopped.
    """
    return (
        "You have no Studio spaces yet. Run edullm studio --project <name> to make one, and "
        "that name is the space: running it again with the same project brings back the same "
        "disk with your files on it, and a different project makes a different space. There is "
        "no default for it, because a default would put two unrelated pieces of work under one "
        "name and one bill -- and Studio spend is the spend nothing else on this platform can "
        "currently see."
    )


def spaces_said(
    spaces: Sequence[OwnedSpace],
    apps: Mapping[str, RunningApp],
    settings: StudioSettings,
) -> list[str]:
    """Everything this person has, what is running, and what the disks cost.

    **THE BARE VERB LISTS RATHER THAN REFUSING, AND THAT IS A REVERSAL WORTH NAMING.** It used
    to answer ``no_project``, which was right when a person had one space and the flag was
    merely required. Now the flag names *which* space, so somebody who has forgotten what they
    called a project last week has no way to find out -- and the tool that knows is the tool
    refusing to say. ``cli/lane.py``'s ``no_machine_to_stop`` already made this ruling for the
    lane: a person who typed the wrong project is answered with the projects they have.

    The running ones are marked because that is the line that costs eleven times what the disk
    does, and the whole verb exists to make that visible.
    """
    if not spaces:
        return [no_spaces_said()]
    lines = ["Your Studio spaces:", ""]
    for space in spaces:
        app = apps.get(space.name)
        size = "size unknown" if space.volume_gib is None else f"{space.volume_gib} GB"
        if app is None:
            state = "stopped"
        else:
            state = f"RUNNING on {app.instance_type or 'a shape it did not report'}"
        reach = (
            f"--project {space.project}"
            if space.project is not None
            else "not reachable by --project; it was not named by this tool"
        )
        lines.append(f"  {space.name}  ({size}, {state})")
        lines.append(f"      {reach}")
    running = [space for space in spaces if space.name in apps]
    total = monthly_volume_cost(spaces, settings)
    lines.append("")
    lines.append(
        f"{len(spaces)} spaces, about ${total:.2f} a month in disk whether or not anything "
        "runs. Nothing deletes a space for you."
    )
    if running:
        lines.append(
            f"{len(running)} of them {'is' if len(running) == 1 else 'are'} billing by the "
            "hour right now. edullm studio --stop stops all of them."
        )
    return lines


def nothing_to_stop(spaces: Sequence[OwnedSpace], *, project: str) -> Refusal:
    """``--stop`` that matched no running app, which is not an error and is worth saying.

    **IT NAMES WHAT IS RUNNING, WHICH IS ``no_machine_to_stop``'S RULING APPLIED HERE.** A
    person runs this because they believe something is billing. Answering a mistyped project
    with a bare "nothing found" tells them the opposite of the truth in the exact words that
    sound like reassurance, and they stop looking.
    """
    if not project:
        return Refusal(
            code="nothing_to_stop",
            detail=(
                "none of your spaces has an app running, so there is no compute to stop and "
                "nothing of yours is billing by the hour. Their volumes are still there and so "
                "are your files."
            ),
        )
    known = sorted(space.project for space in spaces if space.project is not None)
    if not known:
        return Refusal(
            code="nothing_to_stop",
            detail=(
                f"you have no space for {project!r} and no running app anywhere, so nothing "
                "was stopped and nothing of yours is billing by the hour."
            ),
        )
    return Refusal(
        code="nothing_to_stop",
        detail=(
            f"nothing is running for {project!r}, so no compute was stopped and none of it is "
            f"billing by the hour. The projects you have spaces for: {', '.join(known)}. Run "
            "edullm studio --stop with no project to stop everything you do have running."
        ),
    )


def studio_document(
    *,
    request: StudioRequest,
    settings: StudioSettings,
    shape: StudioShape | None,
    app: RunningApp | None,
    spaces: Sequence[OwnedSpace] = (),
    apps: Mapping[str, RunningApp] | None = None,
    idle: IdleShutdown | None = None,
) -> dict[str, Any]:
    """What this verb established, for a caller that is a program rather than a person.

    The same envelope discipline ``cli/machine.py`` applies: a caller branches on the fields
    and prints ``said``, and never parses the paragraphs. ``url`` is deliberately absent --
    a presigned URL is a bearer credential with a short life, and putting one in a document
    somebody redirects to a file is how it ends up in a repository.

    ``idle_timeout_minutes`` is what the domain said rather than what this file believes, and
    ``null`` means either "off" or "could not be read", which are the same thing to a caller
    deciding whether to trust something else to stop an app.
    """
    return {
        "domain_id": settings.domain_id,
        "space": request.space,
        "person": request.person,
        "project": request.project,
        "instance_type": None if shape is None else shape.instance_type,
        "hourly_rate_usd": None if shape is None else str(shape.hourly_rate_usd),
        "volume_gib": settings.volume_gib,
        "app_status": None if app is None else app.status,
        "idle_shutdown": bool(idle and idle.enabled),
        "idle_timeout_minutes": None if idle is None else idle.minutes,
        "spaces": [
            {
                "space": space.name,
                "project": space.project,
                "volume_gib": space.volume_gib,
                "running": space.name in (apps or {}),
            }
            for space in spaces
        ],
    }
