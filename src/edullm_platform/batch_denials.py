"""What the two Phase 3 sessions must not be able to do, and what counts as being refused.

Phase 3 gives this platform two identities it did not have: the admission session now runs
beside a queue that can start compute, and a workload container now runs as a role of its
own inside AWS. Both are claims about committed templates, and both roles are created from
a laptop and are not redeployed by CI -- so a policy widened in the console leaves every
test in this repository green. This module is the other half, and it is the Phase 3
counterpart of :mod:`edullm_platform.admission_denials`.

**Read Phase 1 and Phase 2 first.** The discipline is theirs and is not restated: a failure
is not a denial; a refusal is recognised by its error code and the operation it names rather
than by its wording; a permitted call must not do anything; nothing captured is echoed; and
one run reports every probe rather than stopping at its first anomaly.
:data:`~edullm_platform.publisher_denials.PROBE_SELECTION_LESSONS` is the short version.

**The classifier is Phase 2's, imported rather than spelled again.**
:func:`~edullm_platform.admission_denials.require_denial` already carries per-probe code
sets for the two services that word these answers their own way, which is the only reason
Phase 2 had to write a second copy of Phase 1's. A third copy would be a third chance for
the three to disagree about what a denial is, and there is nothing about Phase 3's probes
that its order of checks does not already handle.

**Two matrices, one shape.** The admission role must be refused ``batch:SubmitJob``,
``batch:TerminateJob``, ``batch:RegisterJobDefinition`` and ``batch:DescribeJobs``: the
GitHub-facing role gains no Batch capability in this phase, and those four are the actions
Phase 3 makes meaningful rather than hypothetical. The workload role must be refused
``s3:PutObject`` on the lineage bucket, ``batch:SubmitJob``, ``states:StartExecution`` and
``ecr:PutImage``: a container that could write lineage records could forge a statement by
the platform, one that could submit or start an execution would be a compute path outside
admission, and one that could push an image could replace the digest it was pinned to.

What the first lesson cost here, and what each probe pays for being answerable:

**``batch:DescribeJobs`` is answered by authorization because Batch does not 404 a describe
of an absent job** -- it returns an empty ``jobs`` array and exit status zero. That makes it
the rare probe that is both inert and unambiguous: permitted looks like success, refused
looks like ``AccessDeniedException``, and existence has no way to answer instead. It is also
the action the recorder Lambda genuinely holds, on ``"*"`` because it has no resource type,
so this entry is the one that says the two identities differ.

**``batch:TerminateJob`` names a job id nothing minted, and the risk is written down rather
than assumed away.** If Batch authorizes first, a refusal is observable and a permitted call
terminates nothing that exists. If it looks the job up first, the probe answers with a
not-found and this module reports ``attempt_failed_for_another_reason`` and refuses to
submit the run -- loudly, rather than filing the not-found as a refusal. That is the failure
direction Phase 1's first lesson demands and it has not been measured against this account.

**``batch:RegisterJobDefinition`` cannot be made inert, and says so.** There is no dry run,
and every form of the call that reaches authorization also registers something if it is
allowed. What is bounded: the name is under this project's prefix and says what it is, the
revision it would create is not referenced by any queue so nothing can run on it, and
``batch:DeregisterJobDefinition`` removes it. What is not bounded: a permitted call creates
one job definition revision. The alternative was a deliberately invalid document, and
whether Batch validates before or after it authorizes is unmeasured -- getting that wrong
makes the probe permanently unprovable, which is the trade Phase 2 already refused for
``s3:PutObject``.

**``ecr:PutImage`` names a repository beside the registered one rather than the registered
one.** A permitted push into the real repository would put an unreviewed manifest under a
tag in the registry the platform pins its digests from, which is exactly the thing the
workload role must not be able to do and exactly the wrong way to find out. The cost is
Phase 1's: this rests on ECR authorizing before it looks the repository up, which is a fact
about ECR today rather than a guarantee, and a run where it does not answers with a
not-found and refuses to submit.

**The state-machine probe proves less than its criterion says, and the gap is real.** The
workload matrix aims ``states:StartExecution`` at a machine name nothing creates, beside the
admission machine, because a permitted call against the real one would start an admission
execution. A role widened to hold ``states:StartExecution`` on the admission machine alone
would therefore still be refused here and reported as narrow. Closing that would mean a
permitted probe starting a real execution, which is worse than the gap.

**No credentials were available when this was written.** Neither matrix has been run against
the account. Everything below says what the deployed policies are written to do, and the
first live run of each should be read with that in mind.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Final, Literal, Self

from pydantic import BeforeValidator, Field, ValidationError, model_validator

from edullm_platform.admission_denials import (
    LINEAGE_BUCKET,
    LINEAGE_RECORD_PREFIXES,
    S3_PERMITTED_ERROR_CODES,
    AdmissionDenialProbe,
    AdmissionSetupError,
    AdmissionStateMachine,
    read_lineage_bucket,
    read_state_machine_arn,
    record_admission_denial,
    require_denial,
)
from edullm_platform.contracts.base import (
    ContractModel,
    parse_str_enum,
    require_ordered_sequence,
)
from edullm_platform.publisher_denials import (
    ABSENT_JOB_DEFINITION,
    ABSENT_JOB_QUEUE,
    ABSENT_REPOSITORY_SUFFIX,
    DENIAL_PROBE_JOB_NAME,
    AttemptedDenial,
    DenialNotProvenError,
    ProbeLesson,
    ProbeOutcome,
    PublisherDenialReason,
    assumed_role_identity,
    run_aws,
)

__all__ = [
    "ABSENT_BATCH_JOB_ID",
    "ADMISSION_BATCH_DENIED_ACTIONS",
    "BATCH_PROBE_LESSONS",
    "DENIAL_PROBE_JOB_DEFINITION",
    "DENIED_ACTIONS_BY_ROLE",
    "ECR_REPOSITORY_PREFIX",
    "ROLE_NAME_BY_ROLE",
    "WORKLOAD_DENIED_ACTIONS",
    "WORKLOAD_LINEAGE_PROBE_KEY",
    "BatchDenialMatrix",
    "BatchDenialMatrixRun",
    "BatchDenialRole",
    "BatchSetupError",
    "BatchSetupReason",
    "attempt_batch_denials",
    "batch_denial_probes",
    "caller_identity",
    "probe_names_a_lineage_record_prefix",
    "read_ecr_repository",
]

#: Every resource this project owns is named for it. An ECR repository outside the prefix
#: is somebody else's, and a refusal read out of their policy would be reported here as a
#: fact about this role.
ECR_REPOSITORY_PREFIX: Final = "sbsandbox-intern-edullm-"

#: The role each matrix is a claim about, as the committed templates spell them. Names
#: rather than ARNs, for Phase 1's reason: a name identifies the thing within one account
#: and an ARN adds the account id.
ADMISSION_ROLE_NAME: Final = "sbsandbox-intern-edullm-admission"
WORKLOAD_ROLE_NAME: Final = "sbsandbox-intern-edullm-batch-workload"

#: A well-formed Batch job id nothing minted. Batch job ids are UUIDs, so a value that is
#: not one could be refused on format before authorization is reached -- the mistake the
#: EC2 ``RunInstances`` probe spent three rounds discovering.
ABSENT_BATCH_JOB_ID: Final = "00000000-0000-4000-8000-00000000dead"

#: The job definition the register probe would create if it were permitted. Under this
#: project's prefix so it cannot collide with another team's, and named for what it is so
#: an operator who finds one knows both what made it and that something is wrong.
DENIAL_PROBE_JOB_DEFINITION: Final = "sbsandbox-intern-edullm-denial-probe"

#: A container definition Batch will accept. Well-formed on purpose: a document Batch
#: rejects might be rejected before the request is authorized, which would turn a real
#: refusal into a validation failure and leave the entry unproven. The image is a public
#: one that is never pulled, because nothing ever runs this definition.
DENIAL_PROBE_CONTAINER_PROPERTIES: Final = json.dumps(
    {"image": "public.ecr.aws/amazonlinux/amazonlinux:2023", "vcpus": 1, "memory": 512},
    separators=(",", ":"),
)

#: Where the workload write probe points inside the real lineage bucket. Its own key rather
#: than the admission matrix's, so an operator who finds an object knows which session
#: wrote it -- and deliberately not under ``intent/``, ``decision/`` or ``conflicts/``,
#: because an object of this project's own making inside one of those would be a forged
#: statement by the platform.
WORKLOAD_LINEAGE_PROBE_KEY: Final = "denial-probe/workload-must-never-write-here.txt"

#: Appended to the admission state machine's name for the workload probe, so a permitted
#: call starts nothing. See the module docstring for what that costs.
ABSENT_STATE_MACHINE_SUFFIX: Final = "-workload-denial-probe-absent"
DENIAL_PROBE_START_NAME: Final = "edullm-workload-denial-probe-must-not-start"

#: A tag nothing publishes under, on a repository that does not exist.
DENIAL_PROBE_IMAGE_TAG: Final = "denial-probe-must-never-exist"

#: A well-formed OCI manifest that references nothing. Well-formed for the same reason the
#: container properties are: a document ECR rejects on parse might be rejected before the
#: request is authorized.
DENIAL_PROBE_IMAGE_MANIFEST: Final = json.dumps(
    {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {
            "mediaType": "application/vnd.oci.image.config.v1+json",
            "size": 0,
            "digest": f"sha256:{'0' * 64}",
        },
        "layers": [],
    },
    separators=(",", ":"),
)

#: The four actions each matrix attempts, in order.
ADMISSION_BATCH_DENIED_ACTIONS: Final = (
    "batch:SubmitJob",
    "batch:TerminateJob",
    "batch:RegisterJobDefinition",
    "batch:DescribeJobs",
)
WORKLOAD_DENIED_ACTIONS: Final = (
    "s3:PutObject",
    "batch:SubmitJob",
    "states:StartExecution",
    "ecr:PutImage",
)


class BatchDenialRole(StrEnum):
    """Which identity a run is a claim about.

    One enum rather than two modules, because the two matrices differ only in their probe
    list and the role they must be run as. Carrying the role in the record is what stops a
    workload matrix being read later as though it said something about the admission role.
    """

    ADMISSION = "admission"
    WORKLOAD = "workload"


DENIED_ACTIONS_BY_ROLE: Final[dict[BatchDenialRole, tuple[str, ...]]] = {
    BatchDenialRole.ADMISSION: ADMISSION_BATCH_DENIED_ACTIONS,
    BatchDenialRole.WORKLOAD: WORKLOAD_DENIED_ACTIONS,
}

ROLE_NAME_BY_ROLE: Final[dict[BatchDenialRole, str]] = {
    BatchDenialRole.ADMISSION: ADMISSION_ROLE_NAME,
    BatchDenialRole.WORKLOAD: WORKLOAD_ROLE_NAME,
}


#: What choosing a Phase 3 probe has cost. Phase 1's list and Phase 2's both still apply;
#: these are what the Batch and workload matrices added. Neither was learned from a run --
#: there are no credentials in the environment this was written in -- so each records what
#: the templates and the services' documented behaviour say, and names the way it would
#: fail if that turns out to be wrong.
BATCH_PROBE_LESSONS: Final[tuple[ProbeLesson, ...]] = (
    ProbeLesson(
        rule=(
            "A read whose absent target answers with an empty result rather than an error "
            "is the strongest probe available, and it is worth going looking for one."
        ),
        learned_from=(
            "Choosing the batch:DescribeJobs probe. Every other entry in these two "
            "matrices trades something -- an unmeasured assumption about authorization "
            "order, or a permitted call that creates an object. DescribeJobs trades "
            "nothing: an absent job id comes back as an empty jobs array with exit status "
            "zero, so a permitted call is unambiguous and inert at the same time."
        ),
        detail=(
            "Phase 1's first lesson is usually read as a warning, and it is also a search "
            "criterion. The lesson says a probe whose target may not exist can be answered "
            "by existence instead of by authorization; the corollary is that an action "
            "whose absent target is answered by an empty result rather than by an error "
            "cannot be, because there is no not-found path for it to take.\n"
            "\n"
            "Three of the four Phase 3 admission probes had to accept a cost, and the "
            "reason this one did not is that it is a list-shaped read. When a matrix has "
            "to cover a service, the read actions are worth enumerating before the write "
            "ones: several of them have this property, and the entry that uses one is the "
            "entry that will still be conclusive in a year."
        ),
    ),
    ProbeLesson(
        rule=(
            "A probe that would create something is written down as one, with what bounds "
            "it and what does not."
        ),
        learned_from=(
            "The batch:RegisterJobDefinition probe. Batch has no dry run for it, and every "
            "form of the call that reaches authorization also registers a revision if it "
            "is allowed."
        ),
        detail=(
            "This is Phase 2's s3:PutObject lesson meeting a second service, and the "
            "answer is the same shape. What is bounded: the name is under this project's "
            "prefix and says what it is; no job queue references the definition, so "
            "nothing can be run on it; and batch:DeregisterJobDefinition removes it. What "
            "is not bounded: one revision exists until somebody deregisters it, and Batch "
            "keeps deregistered revisions visible, so the trace is permanent even after "
            "the cleanup.\n"
            "\n"
            "The alternative considered and rejected was a deliberately malformed "
            "container-properties document, so that a permitted call would fail after "
            "authorization. Whether Batch validates the document before or after it "
            "authorizes the request has not been measured, and guessing wrong makes the "
            "entry permanently unprovable rather than merely costly -- which is the trade "
            "Phase 2 already refused when it chose a real conditional write over a "
            "deliberately wrong content digest."
        ),
    ),
    ProbeLesson(
        rule=(
            "When a probe must aim away from the real resource to stay inert, the claim it "
            "can make gets narrower, and the narrower claim is what goes in the record."
        ),
        learned_from=(
            "The workload matrix's states:StartExecution probe. Aiming it at the real "
            "admission state machine would start an admission execution if the role were "
            "widened, so it names a machine beside it that nothing creates."
        ),
        detail=(
            "What the probe proves is that the workload role cannot start that ARN. What "
            "the criterion wants is that it cannot start anything. The two coincide today "
            "because the workload role's policy names no states action at all, so the "
            "refusal is an implicit deny that would answer the same for any ARN -- but a "
            "role widened to hold states:StartExecution on the admission machine alone "
            "would be refused here and reported as narrow.\n"
            "\n"
            "The gap is recorded rather than closed because closing it means a permitted "
            "probe starting a real execution of the machine that admits runs. Phase 1's "
            "framing applies unchanged: a weaker claim that is always safe beats a "
            "stronger one bought with a call that does something."
        ),
    ),
)


class BatchSetupReason(StrEnum):
    """Why a run could not be attempted at all, as distinct from what it found.

    Every value Phase 2 also has is spelled the same way, so an operator reading a runner
    log does not have to learn a third vocabulary. Two are new: a matrix run under the
    wrong identity, and an ECR repository name the probe cannot use.
    """

    AWS_CLI_UNAVAILABLE = "aws_cli_unavailable"
    CALLER_IDENTITY_UNREADABLE = "caller_identity_unreadable"
    CALLER_IS_NOT_AN_ASSUMED_ROLE = "caller_is_not_an_assumed_role"
    CALLER_IS_NOT_THE_EXPECTED_ROLE = "caller_is_not_the_expected_role"
    STATE_MACHINE_ARN_UNUSABLE = "state_machine_arn_unusable"
    STATE_MACHINE_ARN_NAMES_ANOTHER_MACHINE = "state_machine_arn_names_another_machine"
    STATE_MACHINE_ARN_IS_IN_ANOTHER_REGION = "state_machine_arn_is_in_another_region"
    LINEAGE_BUCKET_UNUSABLE = "lineage_bucket_unusable"
    ECR_REPOSITORY_UNUSABLE = "ecr_repository_unusable"


class BatchSetupError(RuntimeError):
    """The run could not be set up, so nothing was attempted.

    Carries the reason and nothing else. The values it is raised with are argument text and
    identity text -- a state machine ARN holds the account id, and a role name in this
    shared sandbox may be somebody's own -- and the string form of this error reaches a
    world-readable runner log.
    """

    def __init__(self, reason: BatchSetupReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


def _admission_probes(*, region: str) -> tuple[AdmissionDenialProbe, ...]:
    # Four Batch actions and nothing else. The admission role's S3, EC2, IAM and Step
    # Functions refusals are Phase 2's matrix and are still proved there; repeating them
    # here would make one widening show up as two failures and neither as the new one.
    return (
        # Absent queue and absent job definition, both Phase 1's names, because Batch
        # authorizes before it looks either up: a refusal is observable and a permitted
        # call submits nothing.
        AdmissionDenialProbe(
            action="batch:SubmitJob",
            operation="SubmitJob",
            event_source="batch.amazonaws.com",
            resource_name=ABSENT_JOB_QUEUE,
            arguments=(
                "batch",
                "submit-job",
                "--region",
                region,
                "--job-name",
                DENIAL_PROBE_JOB_NAME,
                "--job-queue",
                ABSENT_JOB_QUEUE,
                "--job-definition",
                ABSENT_JOB_DEFINITION,
            ),
        ),
        AdmissionDenialProbe(
            action="batch:TerminateJob",
            operation="TerminateJob",
            event_source="batch.amazonaws.com",
            resource_name=ABSENT_BATCH_JOB_ID,
            arguments=(
                "batch",
                "terminate-job",
                "--region",
                region,
                "--job-id",
                ABSENT_BATCH_JOB_ID,
                "--reason",
                "edullm denial probe: this job does not exist",
            ),
        ),
        AdmissionDenialProbe(
            action="batch:RegisterJobDefinition",
            operation="RegisterJobDefinition",
            event_source="batch.amazonaws.com",
            resource_name=DENIAL_PROBE_JOB_DEFINITION,
            arguments=(
                "batch",
                "register-job-definition",
                "--region",
                region,
                "--job-definition-name",
                DENIAL_PROBE_JOB_DEFINITION,
                "--type",
                "container",
                "--container-properties",
                DENIAL_PROBE_CONTAINER_PROPERTIES,
            ),
        ),
        # The one probe in either matrix that is inert and unambiguous at the same time:
        # Batch answers a describe of an absent job with an empty array rather than an
        # error, so existence has no way to answer instead of authorization.
        AdmissionDenialProbe(
            action="batch:DescribeJobs",
            operation="DescribeJobs",
            event_source="batch.amazonaws.com",
            resource_name=ABSENT_BATCH_JOB_ID,
            arguments=(
                "batch",
                "describe-jobs",
                "--region",
                region,
                "--jobs",
                ABSENT_BATCH_JOB_ID,
            ),
        ),
    )


def _workload_probes(
    *,
    region: str,
    state_machine: AdmissionStateMachine,
    lineage_bucket: str,
    ecr_repository: str,
) -> tuple[AdmissionDenialProbe, ...]:
    return (
        # The real bucket, because a made-up one is answered NoSuchBucket before anybody is
        # authorized; --if-none-match because without it the bucket's own policy refuses
        # every caller and the answer says nothing about this role. Phase 2 learned both.
        AdmissionDenialProbe(
            action="s3:PutObject",
            operation="PutObject",
            event_source="s3.amazonaws.com",
            resource_name=f"{lineage_bucket}/{WORKLOAD_LINEAGE_PROBE_KEY}",
            arguments=(
                "s3api",
                "put-object",
                "--region",
                region,
                "--bucket",
                lineage_bucket,
                "--key",
                WORKLOAD_LINEAGE_PROBE_KEY,
                "--if-none-match",
                "*",
            ),
            permitted_error_codes=S3_PERMITTED_ERROR_CODES,
        ),
        AdmissionDenialProbe(
            action="batch:SubmitJob",
            operation="SubmitJob",
            event_source="batch.amazonaws.com",
            resource_name=ABSENT_JOB_QUEUE,
            arguments=(
                "batch",
                "submit-job",
                "--region",
                region,
                "--job-name",
                DENIAL_PROBE_JOB_NAME,
                "--job-queue",
                ABSENT_JOB_QUEUE,
                "--job-definition",
                ABSENT_JOB_DEFINITION,
            ),
        ),
        AdmissionDenialProbe(
            action="states:StartExecution",
            operation="StartExecution",
            event_source="states.amazonaws.com",
            resource_name=f"{state_machine.name}{ABSENT_STATE_MACHINE_SUFFIX}",
            arguments=(
                "stepfunctions",
                "start-execution",
                "--region",
                region,
                "--state-machine-arn",
                f"{state_machine.arn}{ABSENT_STATE_MACHINE_SUFFIX}",
                "--name",
                DENIAL_PROBE_START_NAME,
            ),
        ),
        # A repository beside the registered one, never the registered one: a permitted
        # push into the real repository would put an unreviewed manifest under a tag in the
        # registry this platform pins its digests from.
        AdmissionDenialProbe(
            action="ecr:PutImage",
            operation="PutImage",
            event_source="ecr.amazonaws.com",
            resource_name=f"{ecr_repository}{ABSENT_REPOSITORY_SUFFIX}",
            arguments=(
                "ecr",
                "put-image",
                "--region",
                region,
                "--repository-name",
                f"{ecr_repository}{ABSENT_REPOSITORY_SUFFIX}",
                "--image-tag",
                DENIAL_PROBE_IMAGE_TAG,
                "--image-manifest",
                DENIAL_PROBE_IMAGE_MANIFEST,
            ),
        ),
    )


def batch_denial_probes(
    *,
    role: BatchDenialRole,
    region: str,
    state_machine: AdmissionStateMachine,
    lineage_bucket: str,
    ecr_repository: str,
) -> tuple[AdmissionDenialProbe, ...]:
    """The matrix for one role, aimed at this account's deployed resources."""
    if role is BatchDenialRole.ADMISSION:
        return _admission_probes(region=region)
    return _workload_probes(
        region=region,
        state_machine=state_machine,
        lineage_bucket=lineage_bucket,
        ecr_repository=ecr_repository,
    )


class BatchDenialMatrix(ContractModel):
    """Every action one Phase 3 session was refused in one run.

    The attempts have to be the whole matrix, in order, for Phase 1's reason: a run that
    proved three of the four refusals proved three of them, and a file able to hold the
    three would be read later as though it had proved all four.

    ``role`` is recorded because the two matrices are the same shape and different claims,
    and a file that did not say which one it was would be readable as either.
    """

    schema_version: Literal[1]
    role: Annotated[BatchDenialRole, BeforeValidator(parse_str_enum(BatchDenialRole))]
    attempts: Annotated[tuple[AttemptedDenial, ...], BeforeValidator(require_ordered_sequence)] = (
        Field(min_length=1, strict=False)
    )

    @model_validator(mode="after")
    def validate_every_action_in_the_matrix_was_attempted(self) -> Self:
        attempted = tuple(attempt.attempted_action for attempt in self.attempts)
        if attempted != DENIED_ACTIONS_BY_ROLE[self.role]:
            raise ValueError("the record must hold one denial per matrix action, in matrix order")
        return self


@dataclass(frozen=True)
class BatchDenialMatrixRun:
    """Every probe's outcome from one run, whether or not the run proved the matrix.

    Phase 2's three members over a different record and one more field. It is spelled again
    rather than subclassed because ``matrix()`` returns a different contract, and an
    override that returned something the base class does not produce would be a lie about
    the base class for the sake of saving ten lines.
    """

    role: BatchDenialRole
    outcomes: tuple[ProbeOutcome, ...]

    @property
    def proven(self) -> bool:
        """Whether every action in the matrix came back as a refusal."""
        return all(outcome.denial is not None for outcome in self.outcomes)

    @property
    def summary(self) -> tuple[str, ...]:
        """One line per action, in matrix order, safe for a world-readable log."""
        return tuple(str(outcome) for outcome in self.outcomes)

    def matrix(self) -> BatchDenialMatrix:
        """The record of the run, which exists only if every action was refused."""
        denials = tuple(outcome.denial for outcome in self.outcomes if outcome.denial is not None)
        if len(denials) != len(self.outcomes):
            raise ValueError("a run that did not refuse every action has no matrix to write")
        return BatchDenialMatrix(schema_version=1, role=self.role, attempts=denials)


@contextmanager
def _phase_three_setup_reasons() -> Iterator[None]:
    """Re-raise Phase 2's argument checks in this module's vocabulary.

    The two readers are reused rather than copied, and they raise Phase 2's error. Every
    reason they raise is spelled identically in :class:`BatchSetupReason`, so the
    translation is by value and a reason added to Phase 2 without a counterpart here fails
    loudly at that boundary rather than escaping as an unhandled type.
    """
    try:
        yield
    except AdmissionSetupError as exc:
        raise BatchSetupError(BatchSetupReason(exc.reason.value)) from exc


def read_ecr_repository(repository: str) -> str:
    """Check the image probe is aimed at a repository name this project could own.

    A name that is not an ECR repository name cannot reach authorization, and one belonging
    to another team in the shared account would read a refusal out of their policy and
    report it as a fact about this role.
    """
    candidate = repository.strip()
    if not candidate.startswith(ECR_REPOSITORY_PREFIX):
        raise BatchSetupError(BatchSetupReason.ECR_REPOSITORY_UNUSABLE)
    return candidate


def caller_identity(*, region: str, role: BatchDenialRole) -> tuple[str, str]:
    """Ask STS who this session is, and refuse to proceed as anybody else.

    ``GetCallerIdentity`` requires no permission and cannot be denied by a policy, so it is
    the one call in this job that says nothing about how wide the role is.

    It is checked as well as read, because each matrix is a claim about one named role. Run
    under any other session -- the other Phase 3 role, a person's own role in the shared
    sandbox -- every probe would be refused and the run would report the role as narrow
    without having tested it once. The offending role name is deliberately not printed:
    per-person roles in this account carry personal names, and this reason reaches a public
    runner log.
    """
    try:
        completed = run_aws(
            ("sts", "get-caller-identity", "--region", region, "--query", "Arn", "--output", "text")
        )
    except DenialNotProvenError as exc:
        if exc.reason is PublisherDenialReason.AWS_CLI_UNAVAILABLE:
            raise BatchSetupError(BatchSetupReason.AWS_CLI_UNAVAILABLE) from exc
        raise BatchSetupError(BatchSetupReason.CALLER_IDENTITY_UNREADABLE) from exc
    if completed.returncode != 0:
        raise BatchSetupError(BatchSetupReason.CALLER_IDENTITY_UNREADABLE)
    try:
        role_name, session_name = assumed_role_identity(completed.stdout)
    except DenialNotProvenError as exc:
        raise BatchSetupError(BatchSetupReason.CALLER_IS_NOT_AN_ASSUMED_ROLE) from exc
    if role_name != ROLE_NAME_BY_ROLE[role]:
        raise BatchSetupError(BatchSetupReason.CALLER_IS_NOT_THE_EXPECTED_ROLE)
    return role_name, session_name


def attempt_batch_denials(
    *,
    role: BatchDenialRole,
    region: str,
    state_machine_arn: str,
    ecr_repository: str,
    lineage_bucket: str = LINEAGE_BUCKET,
) -> BatchDenialMatrixRun:
    """Attempt every action in one matrix and report what each one answered.

    The arguments are checked before the session is, and the session before any probe is
    made, so a typo costs nothing and a run under the wrong identity attempts nothing.
    After that, nothing about one probe stops another: reaching this account costs a
    workflow run under a real session, and a matrix that stopped at its first surprise
    would turn one run into one fact.
    """
    with _phase_three_setup_reasons():
        machine = read_state_machine_arn(state_machine_arn, region=region)
        bucket = read_lineage_bucket(lineage_bucket)
    repository = read_ecr_repository(ecr_repository)
    role_name, session_name = caller_identity(region=region, role=role)
    probes = batch_denial_probes(
        role=role,
        region=region,
        state_machine=machine,
        lineage_bucket=bucket,
        ecr_repository=repository,
    )
    outcomes = [
        _attempt(probe, region=region, role_name=role_name, session_name=session_name)
        for probe in probes
    ]
    return BatchDenialMatrixRun(role=role, outcomes=tuple(outcomes))


def _attempt(
    probe: AdmissionDenialProbe,
    *,
    region: str,
    role_name: str,
    session_name: str,
) -> ProbeOutcome:
    """Make one call and say what it established, without letting it stop the others."""
    attempted_at = datetime.now(tz=UTC)
    try:
        completed = run_aws(probe.arguments, action=probe.action)
        error = require_denial(probe, returncode=completed.returncode, stderr=completed.stderr)
        denial = record_admission_denial(
            probe,
            error,
            region=region,
            role_name=role_name,
            session_name=session_name,
            attempted_at=attempted_at,
        )
    except DenialNotProvenError as unproven:
        return ProbeOutcome(action=probe.action, denial=None, unproven=unproven)
    except ValidationError:
        # A refusal the contract will not hold is a bug here rather than a finding about
        # the role, and it still stops the run: a denial that cannot be written down cannot
        # be evidence. The message stays out of the reason, as everywhere.
        return ProbeOutcome(
            action=probe.action,
            denial=None,
            unproven=DenialNotProvenError(
                PublisherDenialReason.DENIAL_COULD_NOT_BE_RECORDED,
                action=probe.action,
            ),
        )
    return ProbeOutcome(action=probe.action, denial=denial, unproven=None)


def probe_names_a_lineage_record_prefix(probe: AdmissionDenialProbe) -> bool:
    """Whether a probe would write where the lineage record itself lives.

    Checked rather than assumed, because the workload write probe is the one call in either
    matrix that is not inert, and a key under ``intent/``, ``decision/`` or ``conflicts/``
    would be a forged statement by the platform rather than a probe object.
    """
    resource = probe.resource_name or ""
    return any(f"/{prefix}" in resource for prefix in LINEAGE_RECORD_PREFIXES)
