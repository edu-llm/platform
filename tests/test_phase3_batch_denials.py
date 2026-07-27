"""Whether the two Phase 3 matrices would establish anything if they were run.

None of this reaches AWS. What it checks is the part that decides what a live run *means*:
which calls are attempted, whether each one could do something if it were permitted, and
whether the classifier calls the right answers denials. Every one of those has been wrong
in this repository before, and each time the matrix stayed green while proving nothing.

The classifier itself is Phase 2's and is exercised here against Phase 3's probes rather
than re-tested in general. What is specific to this phase is the shape of the matrices and
the two costs the module writes down -- a probe that creates something if it is allowed,
and a probe aimed away from the resource its criterion is about.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from edullm_platform.admission_denials import (
    LINEAGE_BUCKET,
    LINEAGE_RECORD_PREFIXES,
    AdmissionDenialProbe,
    AdmissionStateMachine,
    require_denial,
)
from edullm_platform.batch_denials import (
    ADMISSION_BATCH_DENIED_ACTIONS,
    BATCH_PROBE_LESSONS,
    DENIED_ACTIONS_BY_ROLE,
    ECR_REPOSITORY_PREFIX,
    ROLE_NAME_BY_ROLE,
    WORKLOAD_DENIED_ACTIONS,
    BatchDenialMatrix,
    BatchDenialMatrixRun,
    BatchDenialRole,
    BatchSetupError,
    BatchSetupReason,
    batch_denial_probes,
    read_ecr_repository,
)
from edullm_platform.publisher_denials import (
    AttemptedDenial,
    DenialNotProvenError,
    ProbeLesson,
    ProbeOutcome,
    PublisherDenialReason,
)

REGION = "us-east-1"
ECR_REPOSITORY = "sbsandbox-intern-edullm-olmo-core"
STATE_MACHINE = AdmissionStateMachine(
    arn="arn:aws:states:us-east-1:123456789012:stateMachine:sbsandbox-intern-edullm-admission",
    region=REGION,
    name="sbsandbox-intern-edullm-admission",
)
ATTEMPTED_AT = datetime(2026, 7, 27, 20, 15, 30, tzinfo=UTC)


def probes(role: BatchDenialRole) -> tuple[AdmissionDenialProbe, ...]:
    return batch_denial_probes(
        role=role,
        region=REGION,
        state_machine=STATE_MACHINE,
        lineage_bucket=LINEAGE_BUCKET,
        ecr_repository=ECR_REPOSITORY,
    )


def denial(action: str) -> AttemptedDenial:
    return AttemptedDenial(
        region=REGION,
        role_name="sbsandbox-intern-edullm-admission",
        session_name="denial-probe",
        attempted_action=action,
        attempted_resource=None,
        attempted_at=ATTEMPTED_AT,
        outcome="denied",
        error_code="AccessDeniedException",
        error_message="Access Denied",
        event_name=action.split(":", 1)[1],
        event_source=f"{action.split(':', 1)[0]}.amazonaws.com",
    )


# ---------------------------------------------------------------------------------------
# What each matrix attempts
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("role", list(BatchDenialRole))
def test_each_matrix_attempts_exactly_the_actions_it_claims_to(role: BatchDenialRole) -> None:
    """Mutation: drop a probe and leave the action list alone.

    The record would then be one entry short of what its own contract requires, which is
    the right failure -- but only if the two are compared. Read from both sides here: the
    actions from the declared tuple, the probes from the function that builds them.
    """
    attempted = tuple(probe.action for probe in probes(role))

    assert attempted == DENIED_ACTIONS_BY_ROLE[role]


def test_the_admission_matrix_covers_the_four_batch_actions_phase_three_made_meaningful() -> None:
    """Mutation: keep only ``batch:SubmitJob``, which is the one Phase 1 already probed.

    Submitting is not the whole capability. A role that could terminate a job could stop
    somebody else's run, one that could register a job definition could change what the
    queue runs, and one that could describe jobs holds a read Phase 3 deliberately gave
    only to the recorder.
    """
    assert ADMISSION_BATCH_DENIED_ACTIONS == (
        "batch:SubmitJob",
        "batch:TerminateJob",
        "batch:RegisterJobDefinition",
        "batch:DescribeJobs",
    )


def test_the_workload_matrix_covers_writing_lineage_starting_compute_and_pushing_images() -> None:
    assert WORKLOAD_DENIED_ACTIONS == (
        "s3:PutObject",
        "batch:SubmitJob",
        "states:StartExecution",
        "ecr:PutImage",
    )


@pytest.mark.parametrize("role", list(BatchDenialRole))
def test_every_probe_names_the_operation_its_command_actually_calls(
    role: BatchDenialRole,
) -> None:
    """The classifier refuses a denial whose error names another operation.

    Mutation: copy a probe and change its action without changing its operation. The
    command would be right, the classifier would compare the wrong two names, and the entry
    would report ``attempt_called_another_operation`` forever.
    """
    for probe in probes(role):
        action = probe.action
        operation = probe.operation
        assert operation == action.split(":", 1)[1]


@pytest.mark.parametrize("role", list(BatchDenialRole))
def test_no_probe_can_write_where_the_lineage_record_lives(role: BatchDenialRole) -> None:
    """Mutation: point the workload write probe at ``intent/`` to make it more realistic.

    The write probe is the one call in either matrix that is not inert. An object of this
    project's own making under one of the record prefixes would be a forged statement by
    the platform, which is worse than anything the probe could prove.
    """
    for probe in probes(role):
        resource = probe.resource_name or ""
        assert not any(f"/{prefix}" in resource for prefix in LINEAGE_RECORD_PREFIXES)


def test_the_image_probe_names_a_repository_beside_the_registered_one() -> None:
    """Mutation: aim it at the registered repository.

    A permitted push would then put an unreviewed manifest under a tag in the registry this
    platform pins its digests from -- which is exactly the capability the probe exists to
    disprove, discovered by exercising it.
    """
    probe = next(
        probe
        for probe in probes(BatchDenialRole.WORKLOAD)
        if probe.action == "ecr:PutImage"
    )
    resource = probe.resource_name

    assert resource != ECR_REPOSITORY
    assert resource.startswith(ECR_REPOSITORY)


def test_the_state_machine_probe_names_a_machine_nothing_creates() -> None:
    """Mutation: aim it at the deployed admission machine.

    A permitted call would start a real admission execution. What this costs is written
    down in BATCH_PROBE_LESSONS rather than fixed, because the fix is worse than the gap.
    """
    probe = next(
        probe
        for probe in probes(BatchDenialRole.WORKLOAD)
        if probe.action == "states:StartExecution"
    )

    targeted = probe.arguments[probe.arguments.index("--state-machine-arn") + 1]

    assert targeted != STATE_MACHINE.arn
    assert targeted.startswith(STATE_MACHINE.arn), (
        "the probe lands beside the one machine that matters rather than somewhere "
        "unrelated, so a refusal is about the ARN a widening would most likely name"
    )
    assert probe.resource_name != STATE_MACHINE.name


def test_the_write_probe_sends_the_header_the_bucket_policy_forces() -> None:
    """Phase 2's first lesson, and the one that would break this matrix silently.

    Mutation: drop ``--if-none-match``. The bucket denies an unconditional PutObject to
    every principal in the account, S3 attributes nothing, and the probe would answer
    AccessDenied on every run -- including the runs on which the workload role could write
    whatever it liked.
    """
    probe = next(
        probe
        for probe in probes(BatchDenialRole.WORKLOAD)
        if probe.action == "s3:PutObject"
    )
    arguments = probe.arguments

    assert "--if-none-match" in arguments
    assert arguments[arguments.index("--if-none-match") + 1] == "*"
    assert "--body" not in arguments


def test_the_describe_probe_is_the_one_that_is_inert_and_unambiguous_at_once() -> None:
    """Mutation: replace it with a describe of the real queue.

    An absent job id is answered by an empty array rather than by an error, so existence
    has no way to answer instead of authorization. A describe of a real resource would be
    permitted-and-successful, which proves the same thing, and would stop being inert the
    day somebody added a mutation flag to the command.
    """
    probe = next(
        probe
        for probe in probes(BatchDenialRole.ADMISSION)
        if probe.action == "batch:DescribeJobs"
    )
    arguments = probe.arguments

    assert arguments[0:2] == ("batch", "describe-jobs")
    assert not any(argument.startswith("sbsandbox-") for argument in arguments)


# ---------------------------------------------------------------------------------------
# What the classifier does with Phase 3's answers
# ---------------------------------------------------------------------------------------


def batch_probe(action: str) -> AdmissionDenialProbe:
    return next(
        probe
        for probe in probes(BatchDenialRole.ADMISSION)
        if probe.action == action
    )


def test_a_genuine_refusal_of_the_call_the_probe_made_is_a_denial() -> None:
    error = require_denial(
        batch_probe("batch:SubmitJob"),
        returncode=254,
        stderr=(
            "An error occurred (AccessDeniedException) when calling the SubmitJob "
            "operation: User is not authorized to perform: batch:SubmitJob"
        ),
    )

    assert error.code == "AccessDeniedException"


def test_a_not_found_is_not_a_denial() -> None:
    """The failure the ``batch:TerminateJob`` probe would produce if Batch looked first.

    Mutation: treat any non-zero exit as a refusal. Every probe aimed at an absent resource
    would then pass whatever the role could do, which is the direction Phase 1's first
    lesson is about.
    """
    with pytest.raises(DenialNotProvenError) as raised:
        require_denial(
            batch_probe("batch:TerminateJob"),
            returncode=254,
            stderr=(
                "An error occurred (ClientException) when calling the TerminateJob "
                "operation: Job does not exist"
            ),
        )

    assert raised.value.reason is PublisherDenialReason.ATTEMPT_FAILED_FOR_ANOTHER_REASON


def test_a_permitted_call_is_the_worst_outcome_and_is_reported_as_one() -> None:
    """Mutation: read a zero exit status as "nothing to record".

    A permitted ``batch:RegisterJobDefinition`` means the admission session can change what
    the queue runs, and it exits zero.
    """
    with pytest.raises(DenialNotProvenError) as raised:
        require_denial(
            batch_probe("batch:RegisterJobDefinition"),
            returncode=0,
            stderr="",
        )

    assert raised.value.reason is PublisherDenialReason.ATTEMPT_PERMITTED


def test_a_refusal_naming_another_action_does_not_prove_this_one() -> None:
    with pytest.raises(DenialNotProvenError) as raised:
        require_denial(
            batch_probe("batch:SubmitJob"),
            returncode=254,
            stderr=(
                "An error occurred (AccessDeniedException) when calling the SubmitJob "
                "operation: User is not authorized to perform: batch:DescribeJobs"
            ),
        )

    assert raised.value.reason is PublisherDenialReason.DENIAL_NAMED_ANOTHER_ACTION


def test_a_refusal_from_somebody_elses_resource_policy_says_nothing_about_this_role() -> None:
    with pytest.raises(DenialNotProvenError) as raised:
        require_denial(
            batch_probe("batch:DescribeJobs"),
            returncode=254,
            stderr=(
                "An error occurred (AccessDeniedException) when calling the DescribeJobs "
                "operation: refused by a resource-based policy"
            ),
        )

    assert raised.value.reason is PublisherDenialReason.DENIAL_CAME_FROM_A_RESOURCE_POLICY


# ---------------------------------------------------------------------------------------
# The record a run writes
# ---------------------------------------------------------------------------------------


def run_for(role: BatchDenialRole, *, refuse: int | None = None) -> BatchDenialMatrixRun:
    actions = DENIED_ACTIONS_BY_ROLE[role]
    outcomes = tuple(
        ProbeOutcome(
            action=action,
            denial=None if index == refuse else denial(action),
            unproven=(
                DenialNotProvenError(PublisherDenialReason.ATTEMPT_PERMITTED, action=action)
                if index == refuse
                else None
            ),
        )
        for index, action in enumerate(actions)
    )
    return BatchDenialMatrixRun(role=role, outcomes=outcomes)


@pytest.mark.parametrize("role", list(BatchDenialRole))
def test_a_run_that_refused_everything_has_a_matrix(role: BatchDenialRole) -> None:
    run = run_for(role)

    assert run.proven is True
    assert run.matrix().role is role


@pytest.mark.parametrize("role", list(BatchDenialRole))
def test_a_run_that_missed_one_action_has_no_matrix_to_write(role: BatchDenialRole) -> None:
    """Mutation: let ``matrix()`` return whatever was proved.

    A file holding three of the four refusals would be read later as though it had proved
    all four, which is Phase 1's reason for requiring the whole matrix in order.
    """
    run = run_for(role, refuse=1)

    assert run.proven is False
    with pytest.raises(ValueError, match="no matrix to write"):
        run.matrix()


@pytest.mark.parametrize("role", list(BatchDenialRole))
def test_a_run_reports_every_action_rather_than_stopping_at_the_first(
    role: BatchDenialRole,
) -> None:
    """Reaching this account costs a workflow run or a Batch job.

    Mutation: stop at the first anomaly, which turns one run into one fact.
    """
    run = run_for(role, refuse=0)

    assert len(run.summary) == len(DENIED_ACTIONS_BY_ROLE[role])
    assert run.summary[0].startswith("attempt_permitted:")


def test_a_record_holding_the_wrong_role_s_actions_is_refused_at_load() -> None:
    """Mutation: drop ``role`` from the record.

    The two matrices are the same shape and different claims. A file that did not say which
    one it was could be read as either, and a workload run would be filed as evidence that
    the admission role is narrow.
    """
    with pytest.raises(ValueError, match="one denial per matrix action"):
        BatchDenialMatrix(
            schema_version=1,
            role=BatchDenialRole.WORKLOAD,
            attempts=tuple(denial(action) for action in ADMISSION_BATCH_DENIED_ACTIONS),
        )


def test_a_record_out_of_matrix_order_is_refused_at_load() -> None:
    """Mutation: compare the two as sets.

    Order is what makes the record readable beside the module that produced it, and a set
    comparison would accept a file assembled from two different runs.
    """
    with pytest.raises(ValueError, match="one denial per matrix action"):
        BatchDenialMatrix(
            schema_version=1,
            role=BatchDenialRole.ADMISSION,
            attempts=tuple(
                denial(action) for action in reversed(ADMISSION_BATCH_DENIED_ACTIONS)
            ),
        )


# ---------------------------------------------------------------------------------------
# Setup, which is not a finding about the role
# ---------------------------------------------------------------------------------------


def test_a_repository_outside_this_project_is_a_setup_failure() -> None:
    """Mutation: accept any repository name.

    A probe aimed at another team's repository would read a refusal out of their policy and
    report it as a fact about this role.
    """
    with pytest.raises(BatchSetupError) as raised:
        read_ecr_repository("somebody-elses-repository")

    assert raised.value.reason is BatchSetupReason.ECR_REPOSITORY_UNUSABLE


def test_the_registered_repository_is_accepted() -> None:
    assert read_ecr_repository(f" {ECR_REPOSITORY} ") == ECR_REPOSITORY
    assert ECR_REPOSITORY.startswith(ECR_REPOSITORY_PREFIX)


def test_each_matrix_names_the_role_it_is_a_claim_about() -> None:
    """Mutation: run either matrix under whichever session is to hand.

    Under the wrong identity every probe is refused and the run reports a role it never
    tested as narrow.
    """
    assert ROLE_NAME_BY_ROLE[BatchDenialRole.ADMISSION] == "sbsandbox-intern-edullm-admission"
    assert ROLE_NAME_BY_ROLE[BatchDenialRole.WORKLOAD] == (
        "sbsandbox-intern-edullm-batch-workload"
    )


def test_every_probe_selection_lesson_carries_the_run_that_taught_it() -> None:
    """Mutation: add a lesson with no incident attached.

    A rule with nothing behind it reads as caution and gets skipped, which is why the three
    Phase 1 wrote are worded as they are.
    """
    assert BATCH_PROBE_LESSONS
    for lesson in BATCH_PROBE_LESSONS:
        assert isinstance(lesson, ProbeLesson)
        assert lesson.rule and lesson.learned_from and lesson.detail
