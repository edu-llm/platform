"""Whether the identity a submitted job runs as can read the data that job exists to read.

**The failure this is written about.** ``config/workload-catalog.yaml`` offers
``edullm-data-validate``, whose program reads shards out of ``edullm-landing`` and
``edullm-data``. Every CPU target in ``config/execution-targets.yaml`` runs its container as
``sbsandbox-intern-edullm-batch-workload``, and that role held ``s3:PutObject`` and
``s3:ListBucket`` on the outputs bucket and no ``s3:GetObject`` on anything at all. So the
workload compiled, classified, routed to a lead, was approved, scaled a c7i.8xlarge, pulled
its image and died with ``AccessDenied`` on its first read -- and the catalog entry's own
comment told submitters to pick exactly that profile.

Nothing was red. Every file involved was internally consistent: the catalog offered a
workload, the targets backed the profile, the templates declared a role, and no test
compared the three. This module is that comparison.

**The workload role and not the execution role.** A Batch EC2 job has three identities and
only one of them is what the container's own process runs as. ``execution_role`` in
``config/execution-targets.yaml`` is ECS's identity while it *starts* the task -- it pulls
the image and opens the log stream, and the container never sees its credentials -- so a
read grant there would be held by the wrong principal at the wrong time.
``infra/iam/batch-roles.yaml`` sets that out at length. Everything below therefore reads
``workload_role``, and a reader who came here looking for "the execution role's S3 grants"
is looking at the wrong row.

**Four files, and no two sides of any comparison come from the same one.** Which profiles
exist and are provisioned is read from the catalog; which role each of them runs a container
as is read from the execution targets; what that role may do is read from the CloudFormation
under ``infra/iam/``; and where a registered dataset actually lives is read from the ``uri``
of each entry in ``config/datasets.yaml``. The last one matters most: taking the bucket from
:data:`~edullm_platform.contracts.dataset.PUBLISHED_DATASET_BUCKET` would be reading the same
constant the registry's own validator enforces, so a test that did it would agree with the
registry by construction and could only fail if an IAM template disagreed with a string
literal. Parsing the recorded URIs asks the registry where its corpora are.

That care is not general caution. Two assertions in this repository were found this morning
comparing sets that had both been through one filter, and both were green over the exact
state they existed to refuse.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from typing import Final

from edullm_platform.config import load_yaml
from edullm_platform.contracts.execution import ExecutionTargetCatalog
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.iam_documents import IamPermissionStatement
from edullm_platform.role_drift import TemplateRole, load_template_roles
from tests.infrastructure_support import IAM_ROOT, PROJECT_ROOT, load_template

#: ``s3://<bucket>/<key>``, with the bucket as the only thing this needs out of it. Applied
#: to the ``uri`` each published entry in ``config/datasets.yaml`` records, which is the
#: registry's own statement of where that corpus is rather than a constant restating it.
S3_URI = re.compile(r"^s3://(?P<bucket>[a-z0-9][a-z0-9.-]*)/")

#: How a committed template spells an S3 ARN. The partition is a pseudo-parameter here and
#: ``aws`` in the account; nothing below compares against a deployed role, so the template's
#: spelling is the only one in play and folding it would only hide a template that had
#: stopped using ``Fn::Sub``.
TEMPLATE_S3_ARN: Final = "arn:${AWS::Partition}:s3:::"

class WhyItReadsPastTheLibrary(StrEnum):
    """Which kind of reading past the published library an entry below describes.

    THE DISTINCTION IS ABOUT THE DEVICE THE READ IMPLIES, AND IT IS THE ONLY THING THE TWO
    KINDS DISAGREE ABOUT. Both are a workload's program opening a bucket the registered
    corpora do not live in, so both belong in the same map and both are held to the same
    completeness check. What differs is what a *placement* has to be able to do before the
    read is worth granting, and that is a question with two answers rather than one.

    Written as an enum rather than a boolean, because ``needs_no_accelerator=False`` reads
    as the absence of a property and this is the presence of a different one. A third kind
    will arrive as a member and a test case rather than as a second flag nobody unpicks.
    """

    #: The read *is* the work. A program that opens objects, checks their bytes and writes a
    #: report asks for no device, so the honest placement for it is an unaccelerated one and
    #: the grant belongs on the role an unaccelerated profile runs containers as.
    THE_READ_IS_THE_WORK = "the_read_is_the_work"

    #: The read is the prologue to work that does need a device. A fine-tune loads weights
    #: somebody else trained and then trains on a GPU; the read is inseparable from the
    #: accelerated run it starts, and the grant belongs on the role that run's placement
    #: uses.
    WEIGHTS_TO_START_FROM = "weights_to_start_from"


@dataclass(frozen=True)
class InputBeyondTheLibrary:
    """One bucket a workload's program reads past the library, and why it reads it."""

    kind: WhyItReadsPastTheLibrary
    reason: str


#: Buckets a workload's program reads that are *not* the published dataset library, mapped
#: to why it reads them and to which kind of reading that is.
#:
#: WRITTEN OUT RATHER THAN DERIVED, AND THERE IS NOWHERE TO DERIVE IT FROM. Nothing in
#: ``config/`` binds a workload to an input bucket: the catalog entry carries a repository
#: and a set of bounds, and the submission form's ``dataset_release`` field is chosen
#: independently of ``workload_profile``. So the published library is a fact the registry
#: can be asked for -- which is what the first test below does -- and anything a program
#: reads *beyond* it is a fact about that program, which only somebody who has read the
#: program can write down.
#:
#: A map rather than a set, for the same reason ``UNSUBMITTABLE_BY_DESIGN`` in
#: ``tests/test_submission_form_options.py`` is: an entry a reviewer can argue with beats an
#: omission nobody can see. The completeness test below refuses a key the catalog does not
#: carry, so an entry cannot outlive the workload it describes.
#:
#: THE ENTRIES CARRY A KIND, AND THEY DID NOT UNTIL A SECOND KIND ARRIVED. The map held one
#: workload, and the reachability test below took the whole map as one sort of thing --
#: "every workload in this map is that kind of work by construction". It was not a
#: construction; it was one example generalised, and the second entry is the counterexample.
#: :class:`WhyItReadsPastTheLibrary` is that generalisation withdrawn and replaced by a
#: declaration, so a reader can see which claim each entry is being held to instead of
#: inferring it from the fact that there used to be only one.
INPUT_BUCKETS_BEYOND_THE_DATASET_LIBRARY: Final[dict[str, dict[str, InputBeyondTheLibrary]]] = {
    "edullm-data-validate": {
        "edullm-landing": InputBeyondTheLibrary(
            kind=WhyItReadsPastTheLibrary.THE_READ_IS_THE_WORK,
            reason=(
                "the candidate side of the dataset owner's airlock. A staged corpus and its "
                "manifests arrive there and are checked against the published copy before "
                "anything is promoted, so a validator that cannot read it cannot read the "
                "thing it is validating. guides/edullm-data.md documents --landing-bucket "
                "defaulting to it, and records that listing or reading it is the validator's "
                "first action -- which is also what `--prefix`-less discovery of what is "
                "pending needs."
            ),
        ),
    },
    "olmo-core-train": {
        "edullm-olmo-370m-ckpts": InputBeyondTheLibrary(
            kind=WhyItReadsPastTheLibrary.WEIGHTS_TO_START_FROM,
            reason=(
                "the pretrained base checkpoint a fine-tune starts from. A run that is not "
                "training from scratch loads weights somebody else wrote, and this role "
                "could read exactly two things -- its own run's prefix under the outputs "
                "bucket, and the sealed corpora -- neither of which a checkpoint predating "
                "the run is. infra/iam/batch-gpu-roles.yaml carries the grant and the "
                "argument, including that the proper route is a sealed model/ entry that "
                "does not exist yet and that this pair is to be withdrawn when it does."
            ),
        ),
    },
}


#: Every reader below is cached. Nothing here mutates a file, and the reach helpers ask the
#: same four questions once per (profile, bucket) pair -- sixteen provisioned profiles
#: against a growing registry is enough re-parsing of every template under infra/iam/ to be
#: the slowest module in the suite without it.
@cache
def workload_catalog() -> WorkloadCatalog:
    return load_yaml(PROJECT_ROOT / "config" / "workload-catalog.yaml", WorkloadCatalog)


@cache
def execution_targets() -> ExecutionTargetCatalog:
    return load_yaml(PROJECT_ROOT / "config" / "execution-targets.yaml", ExecutionTargetCatalog)


def provisioned_profiles() -> tuple[str, ...]:
    """Every compute profile the catalog says has somewhere to run, in file order."""
    return tuple(
        profile.name for profile in workload_catalog().compute_profiles if profile.provisioned
    )


def unaccelerated_profiles() -> frozenset[str]:
    """The provisioned profiles that ask for no accelerator.

    Read off ``accelerator`` rather than off the ``cpu-`` name prefix. The prefix is a
    convention and the field is the declaration, and a profile renamed without its field
    changing would silently leave this set.
    """
    return frozenset(
        profile.name
        for profile in workload_catalog().compute_profiles
        if profile.provisioned and profile.accelerator == "cpu"
    )


def accelerated_profiles() -> frozenset[str]:
    """The provisioned profiles that carry a device.

    The complement of the set above over the provisioned profiles, and written as its own
    comprehension rather than as a subtraction so that a third value of ``accelerator``
    joins neither set silently. ``ComputeProfile.accelerator`` is a two-member ``Literal``
    today; a subtraction would put a future third value here by default, which is the wrong
    direction for a set that decides whether a GPU grant is on the right role.
    """
    return frozenset(
        profile.name
        for profile in workload_catalog().compute_profiles
        if profile.provisioned and profile.accelerator == "gpu"
    )


def workload_role_named_by(compute_profile: str) -> str:
    """The identity a container placed on this profile runs as."""
    matching = [
        target
        for target in execution_targets().targets
        if target.compute_profile == compute_profile
    ]
    assert len(matching) == 1, (
        f"config/execution-targets.yaml holds {len(matching)} rows for {compute_profile}, so "
        "which role its containers run as is not a question with one answer"
    )
    return matching[0].workload_role


@cache
def role_declared_as(role_name: str) -> TemplateRole:
    """The one committed template that declares this role.

    Found by scanning ``infra/iam/`` rather than by a name-to-path table, so that a role
    moved between templates is still found and a role named by an execution target and
    declared by no template fails here rather than being skipped.
    """
    declared = [
        role
        for path in sorted(IAM_ROOT.glob("*.yaml"))
        for role in load_template_roles(path)
        if role.role_name == role_name
    ]
    assert len(declared) == 1, (
        f"{len(declared)} templates under infra/iam/ declare {role_name}, which "
        "config/execution-targets.yaml names as a workload role; a container cannot run as "
        "a role nothing creates, and two templates creating it is two stacks fighting"
    )
    return declared[0]


@cache
def buckets_registered_datasets_live_in() -> frozenset[str]:
    """Every bucket ``config/datasets.yaml`` records a published corpus in.

    Read straight out of the file rather than through ``PublishedDatasetReference``, whose
    ``uri`` pattern pins the bucket -- validating through the contract would make the answer
    a restatement of the constant the contract enforces, and the point of asking the registry
    is that it is a different source from the templates on the other side of the comparison.
    """
    document = load_template(PROJECT_ROOT / "config" / "datasets.yaml")
    found: set[str] = set()
    for entry in document.get("published", []):
        matched = S3_URI.match(entry["uri"])
        assert matched is not None, (
            f"{entry['reference_id']} records a uri this reader cannot take a bucket out "
            f"of: {entry['uri']!r}"
        )
        found.add(matched.group("bucket"))
    return frozenset(found)


def allow_statements(role: TemplateRole) -> list[IamPermissionStatement]:
    """Every ``Allow`` this role's inline policies carry, refusing the negated spellings.

    ``NotAction`` and ``NotResource`` with ``Allow`` permit everything *not* listed, so a
    reader that collected their contents would report the narrowest-looking grant in the
    file on the widest possible one -- and here it would report a role as *unable* to read a
    bucket it can in fact read anything in. Refused rather than interpreted, which is the
    same call ``tests/test_dataset_validator_role.py`` makes one role over.
    """
    statements: list[IamPermissionStatement] = []
    for policy in role.inline_policies:
        for statement in policy.statements:
            if statement.effect != "Allow":
                continue
            assert statement.action_match.element == "Action", (
                f"{role.role_name} selects actions by {statement.action_match.element} in an "
                "Allow, which grants everything it does not list"
            )
            assert statement.resource_match.element == "Resource", (
                f"{role.role_name} selects resources by {statement.resource_match.element} "
                "in an Allow, which reaches everything it does not list"
            )
            statements.append(statement)
    return statements


def denied_resources(role: TemplateRole, action: str) -> tuple[str, ...]:
    """Everything a ``Deny`` on this role takes ``action`` away from.

    A read grant that some other statement denies is not a read grant, and an identity
    policy's ``Deny`` beats every ``Allow`` on the same principal. Collected so the reach
    below is what the role can actually do rather than what one statement says.
    """
    return tuple(
        resource
        for policy in role.inline_policies
        for statement in policy.statements
        if statement.effect == "Deny" and action in statement.action_match.actions
        for resource in statement.resource_match.resources
    )


def reads_every_object_in(role: TemplateRole, bucket: str) -> bool:
    """Whether this role may ``GetObject`` any key in ``bucket``.

    The whole bucket rather than some prefix of it, spelled as the exact ``<bucket>/*`` ARN.
    A grant scoped to one corpus is a different and narrower claim, and accepting it here
    would let a role that can read one dataset pass a check about the library -- which is
    the shape of failure this module exists for, one level down.
    """
    object_arn = f"{TEMPLATE_S3_ARN}{bucket}/*"
    if object_arn in denied_resources(role, "s3:GetObject"):
        return False
    return any(
        "s3:GetObject" in statement.action_match.actions
        and object_arn in statement.resource_match.resources
        for statement in allow_statements(role)
    )


def may_enumerate(role: TemplateRole, bucket: str) -> bool:
    """Whether this role may enumerate ``bucket``.

    ``s3:ListBucket`` is a bucket-level action, so the resource is the bucket itself and no
    object ARN can express it. A role that can read a key it already knows and cannot
    enumerate the bucket cannot walk a manifest or discover what is pending, which is the
    first thing a validator does.
    """
    bucket_arn = f"{TEMPLATE_S3_ARN}{bucket}"
    if bucket_arn in denied_resources(role, "s3:ListBucket"):
        return False
    return any(
        "s3:ListBucket" in statement.action_match.actions
        and bucket_arn in statement.resource_match.resources
        for statement in allow_statements(role)
    )


@cache
def profiles_that_can_read(bucket: str) -> frozenset[str]:
    """Every provisioned compute profile whose workload role can both read and list it."""
    return frozenset(
        profile
        for profile in provisioned_profiles()
        for role in (role_declared_as(workload_role_named_by(profile)),)
        if reads_every_object_in(role, bucket) and may_enumerate(role, bucket)
    )


# --------------------------------------------------------------------------------------
# The published library, which every profile has to reach because any profile can be sent
# a run that names a corpus
# --------------------------------------------------------------------------------------


def test_every_provisioned_profile_can_read_the_buckets_registered_datasets_live_in() -> None:
    """THE ONE THAT WOULD HAVE CAUGHT IT. Mutation: remove ``read-the-dataset-airlock`` from
    ``infra/iam/batch-roles.yaml``, which is the state this repository was in this morning.

    Asked of every provisioned profile and not only of the ones whose name starts ``gpu-``,
    because ``dataset_release`` is a field of its own on the submission form. It is chosen
    independently of ``workload_profile`` and independently of ``compute_profile``, and
    ``execution.py`` puts ``EDULLM_DATASET_ID`` and ``EDULLM_DATASET_VERSION`` into the
    container for whichever combination arrives. So a role that cannot read the library is
    not a role that runs a different kind of work; it is a placement at which an ordinary
    submission fails, and which one it is depends on a dropdown.

    Both halves are required and they fail differently. Without ``s3:GetObject`` a run
    cannot open a shard whose key it already has. Without ``s3:ListBucket`` -- a
    bucket-level action no object ARN can scope -- it cannot walk the manifest to find out
    which shards there are, and the denial arrives from a call that looks unrelated to the
    corpus it was reading.
    """
    required = buckets_registered_datasets_live_in()
    profiles = provisioned_profiles()

    assert required, (
        "config/datasets.yaml registers no published corpus, so the loop below asks nothing "
        "of any role; the sixteen entries this was written against all live in edullm-data"
    )
    assert profiles, "no compute profile is provisioned, so no role is examined at all"

    unreachable = {
        (profile, bucket)
        for profile in profiles
        for bucket in sorted(required)
        if profile not in profiles_that_can_read(bucket)
    }

    assert not unreachable, (
        f"{sorted(unreachable)} are (compute profile, bucket) pairs where the workload role "
        "config/execution-targets.yaml names cannot read a bucket config/datasets.yaml says "
        "a registered corpus lives in. A submission naming that corpus and that profile is "
        "admitted, approved, placed and then denied on its first read"
    )


def test_the_two_workload_roles_agree_about_the_published_library() -> None:
    """Mutation: grant the library to one workload role and not the other.

    The convergence, asserted rather than left to the comment that argues for it. The split
    that existed until today was an accident of dates -- the GPU role got the library in
    Phase 4 because a training run reads a corpus, and the CPU role was left without it
    because the only CPU workload in the catalog at the time read nothing -- and neither
    half of that is a fact about accelerators.

    The test above already fails on any role that cannot reach the library, so this one is
    redundant *while every profile is required to reach it*. It is here for the direction
    that is not redundant: if a later change ever gives the two roles different dataset
    reach, the failure should name the asymmetry rather than name whichever profile happened
    to be listed first.

    Written over the roles the targets actually name, so a third workload role arriving is
    included without anybody adding it here.
    """
    roles = {workload_role_named_by(profile) for profile in provisioned_profiles()}
    required = buckets_registered_datasets_live_in()

    assert len(roles) > 1, (
        "every provisioned profile runs its containers as the same role, so there is no "
        "asymmetry left for this to be about and the assertion below is vacuous"
    )
    reach = {
        role_name: frozenset(
            bucket
            for bucket in required
            if reads_every_object_in(role_declared_as(role_name), bucket)
        )
        for role_name in sorted(roles)
    }

    assert len(set(reach.values())) == 1, (
        f"the workload roles reach different parts of the published dataset library: "
        f"{ {name: sorted(buckets) for name, buckets in reach.items()} }. Which corpora a "
        "run can open would then depend on the compute profile it was placed on"
    )


# --------------------------------------------------------------------------------------
# What a particular workload reads beyond the library
# --------------------------------------------------------------------------------------


def test_the_declared_extra_inputs_name_workloads_the_catalog_actually_offers() -> None:
    """Mutation: leave an entry behind for a workload that has been renamed or removed.

    The registry is the hand-written side of the comparison below, so it is the side that
    can go stale silently. An entry naming nothing is an entry excusing nothing and quietly
    dropping a bucket out of the check; a reason left blank is the omission the map exists
    to replace.

    The catalog is read whole rather than filtered to the submittable entries. ``dolma`` has
    no registration and its workload cannot run today, and if somebody writes down that
    ``dolma-tokenize`` reads a bucket, that claim should be checked when the registration
    lands rather than skipped until then.
    """
    declared = {workload.name for workload in workload_catalog().workloads}
    named = set(INPUT_BUCKETS_BEYOND_THE_DATASET_LIBRARY)

    assert named, (
        "no workload declares an input beyond the published library, so the test below "
        "iterates nothing; edullm-data-validate and edullm-landing are what it was written "
        "about"
    )
    assert named <= declared, (
        f"{sorted(named - declared)} are named here and are not in "
        "config/workload-catalog.yaml, so the buckets recorded for them are being required "
        "of a role on behalf of a workload nobody can submit"
    )
    for workload, buckets in INPUT_BUCKETS_BEYOND_THE_DATASET_LIBRARY.items():
        assert buckets, f"{workload} is listed with no bucket, which records nothing"
        for bucket, input_ in buckets.items():
            assert input_.reason.strip(), f"{workload} reads {bucket} for no stated reason"
        assert not (set(buckets) & buckets_registered_datasets_live_in()), (
            f"{workload} lists a bucket the dataset registry already covers; the check above "
            "requires that of every profile, and repeating it here would make this map look "
            "like the thing that was holding it"
        )


def inputs_of_kind(kind: WhyItReadsPastTheLibrary) -> tuple[tuple[str, str, str], ...]:
    """Every ``(workload, bucket, reason)`` in the map read for ``kind``, in a stable order."""
    return tuple(
        (workload, bucket, input_.reason)
        for workload, buckets in sorted(INPUT_BUCKETS_BEYOND_THE_DATASET_LIBRARY.items())
        for bucket, input_ in sorted(buckets.items())
        if input_.kind is kind
    )


def test_a_workload_reading_past_the_library_is_runnable_without_an_accelerator() -> None:
    """THE OTHER HALF, AND THE ONE THE CATALOG COMMENT RESTS ON. Mutation: move the airlock
    read to ``infra/iam/batch-gpu-roles.yaml``, so the validator works everywhere except the
    profile it is told to pick.

    Reading objects out of S3 and checking them asks for no device, so a workload whose
    program does that and nothing else has to have a provisioned profile without an
    accelerator whose role can reach the bucket. If there is not, the honest description of
    the entry is that the only way to run it is to rent a GPU to do arithmetic on bytes, at
    between $0.53 and $55.04 an hour depending on which one the submitter picks.

    The unaccelerated profiles are derived from the catalog's own ``accelerator`` field
    rather than named, so promoting a second CPU shape does not turn this red and renaming
    ``cpu-32vcpu`` does not turn it green by accident.

    THIS IS ALSO WHAT KEEPS THE CATALOG COMMENT HONEST. ``edullm-data-validate`` tells a
    submitter to pick ``cpu-32vcpu`` and says the choice is load-bearing rather than advice.
    That sentence is only true while the CPU role is the one that holds the landing-zone
    read, and it is this assertion that fails if somebody moves it.

    **THIS ASKED THE WHOLE MAP UNTIL A SECOND KIND OF ENTRY ARRIVED, AND THE JUSTIFICATION
    IT GAVE FOR DOING SO WAS THE THING THAT WAS WRONG.** It said every workload in the map
    is device-free work "by construction -- it reads a bucket the training corpora do not
    live in". Reading past the library is not a construction that yields anything about
    devices; it was one entry generalised, and it held only because that entry was a
    validator.

    A base-checkpoint read is the counterexample. ``olmo-core-train`` reads
    ``edullm-olmo-370m-ckpts`` to load the weights a fine-tune starts from and then trains
    on a GPU, so the read is inseparable from accelerated work and there is no placement
    for it that a CPU shape could serve.

    WHY THE ANSWER WAS NOT TO GRANT THE CPU ROLE AND MOVE ON, WHICH IS THE CHEAPER EDIT AND
    THE ONE TO REFUSE. Adding ``edullm-olmo-370m-ckpts`` to the CPU workload role would have
    turned this green in one line, and it would have widened the reach of every container
    placed on ``cpu-32vcpu`` to a bucket no CPU workload in the catalog reads -- a real
    grant made to satisfy a test's assumption rather than a workload's need. The map's
    neighbour below exists precisely to catch grants nobody can name a reason for, so buying
    this one's silence would have been paid for out of that one. What changed instead is the
    assumption: the entries declare their kind, and each kind is held to the claim that is
    true of it.
    """
    without_an_accelerator = unaccelerated_profiles()
    reading_is_the_work = inputs_of_kind(WhyItReadsPastTheLibrary.THE_READ_IS_THE_WORK)

    assert without_an_accelerator, (
        "no provisioned profile declares accelerator: cpu, so there is no unaccelerated "
        "placement for anything and the assertion below cannot hold for the right reason"
    )
    assert reading_is_the_work, (
        "no entry in INPUT_BUCKETS_BEYOND_THE_DATASET_LIBRARY reads past the library for "
        "work that needs no device, so this iterates nothing; edullm-data-validate and "
        "edullm-landing are the case it was written about, and an entry losing that kind "
        "should be an edit somebody argued for rather than this test going quiet"
    )
    for workload, bucket, reason in reading_is_the_work:
        reachable = profiles_that_can_read(bucket)
        assert reachable & without_an_accelerator, (
            f"{workload} reads {bucket} -- {reason} -- and no provisioned profile "
            f"without an accelerator can. Profiles that can: {sorted(reachable)}. A "
            "submitter has to pick one of those or meet AccessDenied, and the catalog "
            "entry cannot tell them to pick a CPU shape"
        )


def test_a_workload_reading_weights_to_start_from_can_reach_them_where_it_trains() -> None:
    """THE SECOND KIND, AND IT IS THE MIRROR OF THE TEST ABOVE RATHER THAN AN EXEMPTION FROM
    IT. Mutation: put the base-checkpoint read on ``infra/iam/batch-roles.yaml`` alone, so
    the grant exists, the role diff looks right, and every fine-tune still dies on its first
    read because a training run is not placed on a CPU shape.

    That mutation is not hypothetical and it is the one a reader should expect. The cheapest
    way to make the assertion above pass over a base-checkpoint entry was to grant the CPU
    workload role, and a tree that had done so would hold a grant on the wrong role, a green
    suite, and a researcher meeting ``AccessDenied`` after the queue wait and the approval.
    So the claim asked here is the one that is actually load-bearing for this kind of entry:
    the placement that can reach the bucket has to be one the work can run on.

    Both sets come off the catalog's ``accelerator`` field, so the two tests cannot disagree
    about what a device is, and neither can be satisfied by renaming a profile.

    WHAT THIS DOES NOT ASSERT, DELIBERATELY. It does not require that *no* unaccelerated
    profile reaches the bucket. A CPU workload that legitimately needed these weights -- a
    checksum, an inventory, a conversion -- would be a second entry carrying the other kind,
    and forbidding the reach here would refuse it on behalf of a decision nobody has made.
    What is refused is the reach arriving with no entry at all, which the test below owns.

    THIS TEST IS WITHDRAWN WITH THE GRANT. ``edullm-olmo-370m-ckpts`` is a named exception
    standing in for a sealed ``model/`` entry that does not exist; when that entry lands the
    weights become an ordinary library read, the map entry goes, and the guard below turns
    this red rather than letting it sit measuring an empty set.
    """
    with_an_accelerator = accelerated_profiles()
    weights = inputs_of_kind(WhyItReadsPastTheLibrary.WEIGHTS_TO_START_FROM)

    assert with_an_accelerator, (
        "no provisioned profile declares accelerator: gpu, so there is no accelerated "
        "placement for anything and the assertion below cannot hold for the right reason"
    )
    assert weights, (
        "no entry in INPUT_BUCKETS_BEYOND_THE_DATASET_LIBRARY reads weights to start from, "
        "so this iterates nothing. If the base-checkpoint grant in "
        "infra/iam/batch-gpu-roles.yaml has been withdrawn for the sealed model/ entry it "
        "names, delete this test with it rather than leaving it green over nothing"
    )
    for workload, bucket, reason in weights:
        reachable = profiles_that_can_read(bucket)
        assert reachable & with_an_accelerator, (
            f"{workload} reads {bucket} -- {reason} -- and no provisioned profile with an "
            f"accelerator can. Profiles that can: {sorted(reachable)}. The read is the "
            "prologue to training, so a grant that does not reach the placement the "
            "training runs on is a grant on the wrong role"
        )


def test_nothing_the_workload_roles_read_past_the_library_is_undeclared() -> None:
    """Reads BOTH sides. Mutation: grant a workload role read on a bucket nobody wrote down.

    Every test above asks whether a role can reach what a workload needs. This asks the
    reverse, which is the question a review of the diff would ask: is there anything these
    roles can read that no workload here claims to want? A grant nobody can point at a
    reason for is either a widening that arrived inside a well-intentioned change, or a
    workload whose inputs never got written into the map above -- and the two look identical
    until somebody asks.

    The outputs bucket is excluded because it is not an input. Reading it back is the GPU
    role resuming from a checkpoint it wrote, which ``tests/test_phase5_team_isolation.py``
    owns in full, and folding it in here would make this module a second opinion about the
    team prefix shape.
    """
    outputs_and_library = buckets_registered_datasets_live_in() | {
        "sbsandbox-intern-edullm-outputs"
    }
    declared = {
        bucket
        for buckets in INPUT_BUCKETS_BEYOND_THE_DATASET_LIBRARY.values()
        for bucket in buckets
    }
    reachable: set[tuple[str, str]] = set()
    for profile in provisioned_profiles():
        role_name = workload_role_named_by(profile)
        role = role_declared_as(role_name)
        for statement in allow_statements(role):
            for resource in statement.resource_match.resources:
                if not resource.startswith(TEMPLATE_S3_ARN):
                    continue
                bucket = resource.removeprefix(TEMPLATE_S3_ARN).split("/", maxsplit=1)[0]
                if bucket not in outputs_and_library | declared:
                    reachable.add((role_name, bucket))

    assert not reachable, (
        f"{sorted(reachable)} are (role, bucket) pairs a workload role can reach and no "
        "workload in config/workload-catalog.yaml is recorded as reading. Either the grant "
        "is a widening, or the workload that needs it belongs in "
        "INPUT_BUCKETS_BEYOND_THE_DATASET_LIBRARY with the reason"
    )
