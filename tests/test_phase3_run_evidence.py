"""What live runs left behind, read from the records somebody committed.

Every other Phase 3 test stops at the edge of the AWS call: it reads a template, an ASL
document, a catalog, or a projection built from a synthetic event. These read what came
back from real submissions -- the Batch jobs, the lines their containers printed, what S3
attests about every lineage object they wrote, the sessions that started them, and one
submission that admission refused before anything could be launched.

The cases are in two halves and the second is the point, exactly as in
``test_phase1_run_evidence.py``.

The first half asks what the committed records say, and asks it in the terms the
acceptance criteria are written in rather than field by field.

The second half asks what happens when they stop being true. Each record is a statement
about one moment, and every claim resting on it has to expire rather than quietly go on
reading as proof. Expiry is exercised with fixtures whose ``observed_at`` this module
writes, on both sides of the window, because waiting thirty days is not a test. The joins
are exercised the same way: a Batch job filed under another run, a log stream belonging to
another container, an attempt naming another job and a refusal that failed to prevent a
submission each have to produce a problem rather than an exception or a pass.

**One committed record is deliberately broken and stays that way.** Three runs were
written before the ``"Result": null`` fix in the admission ASL and carry a whole admission
payload where a fan-out size belongs. The lineage store is write-once, so no future
capture will ever repair them. They are recorded as attested, versioned and unloadable,
which is the honest description, and the run holding one is reported as not traceable.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from edullm_platform.evidence import (
    AWS_ACCOUNT_ID_PATTERN,
    CAPTURE_SUFFIX,
    FRESHNESS_WINDOW,
    CaptureLoadVerdict,
    redact_content_digests,
    scan_for_secrets,
)
from edullm_platform.phase1_capture import CaptureVerdict, read_committed_role_captures
from edullm_platform.phase3_capture import (
    PHASE3_CAPTURE_DIR,
    RUNS_SUBDIR,
    TRACEABLE_ARTIFACTS,
    CommittedPhase3Evidence,
    CommittedPhase3Run,
    read_committed_phase3_evidence,
)
from edullm_platform.phase3_evidence import PHASE3_ROLE_TEMPLATES

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CAPTURES = PROJECT_ROOT / PHASE3_CAPTURE_DIR
ONE_SECOND = timedelta(seconds=1)
ONE_MINUTE = timedelta(minutes=1)

#: The queue and environment this project deploys. Named here so a capture pointed at
#: somebody else's Batch resources in this shared account fails on the name.
COMPUTE_ENVIRONMENT = "sbsandbox-intern-edullm-cpu"
JOB_QUEUE = "sbsandbox-intern-edullm-cpu"
JOB_DEFINITION = "sbsandbox-intern-edullm-cpu-run"


@pytest.fixture(scope="module")
def evidence() -> CommittedPhase3Evidence:
    return read_committed_phase3_evidence(PROJECT_ROOT)


@pytest.fixture(scope="module")
def succeeded(evidence: CommittedPhase3Evidence) -> CommittedPhase3Run:
    runs = evidence.runs_with_outcome("succeeded")
    assert runs, "no committed run succeeded, so criterion 1 has nothing to read"
    return runs[0]


@pytest.fixture(scope="module")
def failed(evidence: CommittedPhase3Evidence) -> CommittedPhase3Run:
    """The run whose command returned non-zero, as distinct from the one Batch killed.

    Both are ``failed`` in the lineage, and telling them apart is the whole of what
    criteria 4 and 8 separately assert -- so the fixtures select on the difference rather
    than on position, which would silently swap them when a run is added.
    """
    runs = [
        run
        for run in evidence.runs_with_outcome("failed")
        if run.job is not None and run.job.container_exit_code is not None
    ]
    assert runs, "no committed run failed with a container exit code (criterion 4)"
    return runs[0]


@pytest.fixture(scope="module")
def timed_out(evidence: CommittedPhase3Evidence) -> CommittedPhase3Run:
    runs = [run for run in evidence.runs if run.job is not None and run.job.timed_out]
    assert runs, "no committed run was stopped by its timeout (criterion 8)"
    return runs[0]


@pytest.fixture(scope="module")
def refused(evidence: CommittedPhase3Evidence) -> CommittedPhase3Run:
    runs = evidence.runs_with_outcome("refused")
    assert runs, "no committed run was refused, so criterion 9 has nothing to read"
    return runs[0]


def copy_captures(directory: Path) -> Path:
    shutil.copytree(CAPTURES, directory, dirs_exist_ok=True)
    return directory


def observed(age: timedelta) -> str:
    return (datetime.now(tz=UTC) - age).isoformat().replace("+00:00", "Z")


def rewrite(path: Path, **fields: object) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(fields)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def record_path(directory: Path, run_id: str, name: str) -> Path:
    return directory / RUNS_SUBDIR / run_id / f"{name}{CAPTURE_SUFFIX}"


def aged(directory: Path, age: timedelta) -> CommittedPhase3Evidence:
    """The committed records again, as if they had been observed ``age`` ago."""
    copy_captures(directory)
    for path in sorted(directory.rglob(f"*{CAPTURE_SUFFIX}")):
        rewrite(path, observed_at=observed(age))
    return read_committed_phase3_evidence(PROJECT_ROOT, directory=directory)


def reasons(run: CommittedPhase3Run) -> set[str]:
    return {problem.reason for problem in run.problems}


# --------------------------------------------------------------------------------------
# What the committed records say
# --------------------------------------------------------------------------------------


def test_every_committed_run_capture_holds(evidence: CommittedPhase3Evidence) -> None:
    """Loading and agreeing is the assertion; a stale record refuses to load at all."""
    assert evidence.holds, [
        (problem.record, problem.reason)
        for run in evidence.runs
        for problem in run.problems
    ] + [(problem.record, problem.reason) for problem in evidence.problems]
    assert evidence.runs


def test_a_real_container_ran_to_succeeded_under_the_run_id_that_asked_for_it(
    succeeded: CommittedPhase3Run,
) -> None:
    """Criterion 1. The mutation is reading the lineage outcome and calling it a run.

    A result record saying ``succeeded`` is the platform's own projection of an event. It
    would say exactly the same thing if the projection were wrong, so on its own it
    establishes that this code path was taken rather than that a container ran. What
    distinguishes them is Batch reporting SUCCEEDED with exit code 0 for a job whose name
    is the run id, which is the service's answer rather than ours.
    """
    job = succeeded.job
    assert job is not None
    assert job.status == "SUCCEEDED"
    assert job.container_exit_code == 0
    assert job.succeeded
    assert job.batch_job_name == succeeded.run_id
    assert job.job_queue_name == JOB_QUEUE
    assert job.job_definition_name.startswith(JOB_DEFINITION)
    # It started and it stopped, in that order. A job that never placed carries neither,
    # and would otherwise read here as one that ran instantaneously.
    assert job.started_at is not None
    assert job.stopped_at is not None
    assert job.started_at <= job.stopped_at
    assert succeeded.outcome == "succeeded"


def test_the_container_output_is_readable_through_the_stream_the_job_recorded(
    succeeded: CommittedPhase3Run,
) -> None:
    """Criterion 2. The mutation is recording the log group rather than the stream.

    A group name reads as complete and resolves to every job on the queue, so a record
    carrying one looks healthy and locates nothing. Only fetching the recorded stream back
    and finding a line the container printed tells the two apart, which is why this
    asserts on the lines and on the stream belonging to this job rather than on the field
    being populated.
    """
    logs = succeeded.logs
    job = succeeded.job
    assert logs is not None
    assert job is not None
    assert logs.log_stream_name == job.log_stream_name
    assert logs.log_group_name.startswith("/aws/batch/")
    # A stream, not a group: the group is a prefix of every stream on the queue and the
    # two are different lengths of the same path.
    assert logs.log_stream_name != logs.log_group_name
    assert logs.lines
    assert any("edullm" in line for line in logs.lines)
    assert not logs.truncated


def test_the_result_the_attempt_and_the_batch_job_each_name_the_next(
    succeeded: CommittedPhase3Run,
) -> None:
    """Criterion 3. The mutation is a chain that agrees at the ends and not in the middle.

    Three links: the result names an attempt, the attempt names a scheduler job, and the
    job's name is the run id. Checking only the first and last would pass while the middle
    pointed at somebody else's job, which is exactly the shape of a lineage record that
    describes the wrong container.
    """
    result = succeeded.body("result")
    attempt = succeeded.body("attempt")
    job = succeeded.job
    assert result is not None and attempt is not None and job is not None
    assert result["run_id"] == succeeded.run_id
    assert result["attempt_id"] == attempt["attempt_id"]
    assert attempt["scheduler_job_id"] == job.batch_job_id
    assert job.batch_job_name == succeeded.run_id
    assert result["outcome"] == attempt["terminal_state"] == "succeeded"
    # Where the output went is recorded. The prefix these runs carry predates
    # output_prefix()'s teams/{team}/runs/{run_id} fix, so what is asserted is that the
    # location was recorded at all rather than that it has the shape the code now emits.
    assert result["output_prefixes"]
    assert all(
        prefix.startswith("s3://") and succeeded.run_id in prefix
        for prefix in result["output_prefixes"]
    )


def test_a_deliberate_non_zero_exit_reached_failed_with_the_code_preserved(
    failed: CommittedPhase3Run,
) -> None:
    """Criterion 4. The mutation is projecting every terminal state to succeeded.

    The lineage half of that is caught by the projection tests. What only a real job can
    establish is that Batch reports a non-zero container exit the way this platform reads
    it: the command exited 3 deliberately, and the captured job carries that 3 rather than
    a generic failure. A projection that lost the distinction between "the container
    returned an error" and "the scheduler killed it" would still show FAILED here, so the
    exit code is asserted and not merely the status.
    """
    job = failed.job
    assert job is not None
    assert job.status == "FAILED"
    assert job.container_exit_code == 3
    assert not job.succeeded
    assert job.status_reason
    assert failed.outcome == "failed"
    result = failed.body("result")
    attempt = failed.body("attempt")
    assert result is not None and attempt is not None
    assert result["outcome"] == attempt["terminal_state"] == "failed"
    # The failure joins to the same job the success path joins to, so the two runs are
    # read the same way and only the outcome differs.
    assert attempt["scheduler_job_id"] == job.batch_job_id


def test_the_failing_container_printed_its_own_line_before_exiting(
    failed: CommittedPhase3Run,
) -> None:
    """Criterion 2's other half: logs survive a failure, which is when they are needed.

    The mutation is a log configuration that works for a job that exits zero and loses the
    stream for one that does not -- which is the only case anybody reads logs for.
    """
    logs = failed.logs
    assert logs is not None
    assert logs.lines
    assert any("edullm" in line for line in logs.lines)


def test_a_runaway_job_was_stopped_by_the_timeout_the_manifest_asked_for(
    timed_out: CommittedPhase3Run,
) -> None:
    """Criterion 8. The mutation is a Timeout block that is sent and never enforced.

    That every submission carries a Timeout is proved locally against the submit request,
    and it is the half that usually rots. It is also the half that cannot fail visibly: a
    duration Batch ignores looks identical in the request. What only a real job can show
    is the service acting on it -- a command that would have run for 600 seconds stopped
    at the 180 it was given.

    The exit code is asserted absent, and that is the load-bearing part. A job the
    scheduler killed never got to return a status, so ``container_exit_code`` is None;
    anything else here would mean the command finished on its own and the timeout was a
    coincidence.
    """
    job = timed_out.job
    binding = timed_out.body("binding")
    assert job is not None and binding is not None
    assert job.status == "FAILED"
    assert job.timed_out
    assert job.container_exit_code is None
    assert job.status_reason == "Job attempt duration exceeded timeout"
    # Batch enforced the bound the platform sent, rather than one of its own.
    assert binding["attempt_duration_seconds"] == 180
    # And it really did outrun it: the job ran for at least its allowance, which a job
    # that failed instantly for some other reason would not have.
    assert job.started_at is not None and job.stopped_at is not None
    ran_for = (job.stopped_at - job.started_at).total_seconds()
    assert ran_for >= binding["attempt_duration_seconds"]


def test_a_timeout_and_a_non_zero_exit_are_distinguishable_in_the_record(
    timed_out: CommittedPhase3Run, failed: CommittedPhase3Run
) -> None:
    """The tripwire that keeps criteria 4 and 8 from resting on the same evidence.

    Both runs are ``failed`` in the lineage store, so a reader with only the result record
    cannot tell a workload that broke from one that was killed for running too long. The
    mutation this catches is the two collapsing into one observation -- at which point
    whichever criterion is checked second passes for free.
    """
    killed = timed_out.job
    broke = failed.job
    assert killed is not None and broke is not None
    assert timed_out.outcome == failed.outcome == "failed"
    assert killed.timed_out and not broke.timed_out
    assert killed.container_exit_code is None
    assert broke.container_exit_code == 3
    assert killed.status_reason != broke.status_reason
    assert timed_out.run_id != failed.run_id


def test_a_profile_with_nowhere_to_run_was_refused_and_started_nothing(
    refused: CommittedPhase3Run,
) -> None:
    """Criterion 9. The mutation is a refusal that refuses and submits anyway.

    Two halves, and only the first is easy. The decision has to say no, and no Batch job
    may exist under that run id -- an absence that means nothing unless somebody searched
    every status for it, because ListJobs answers one status at a time and a job refused
    on paper but submitted in fact would be sitting in RUNNABLE.
    """
    refusal = refused.refusal
    assert refusal is not None
    assert refusal.decision_accepted is False
    assert refusal.decision_reason == "no_execution_target"
    assert "unprovisioned_compute_profile" in refusal.decision_detail
    assert refusal.matching_batch_job_ids == ()
    assert refusal.searched_every_status
    assert refusal.refused_and_started_nothing
    # The state machine refused rather than crashed, and said which.
    assert refusal.execution_status == "FAILED"
    assert refusal.execution_error == "AdmissionRejected"


def test_a_refused_run_wrote_its_intent_and_decision_and_nothing_past_them(
    refused: CommittedPhase3Run,
) -> None:
    """The lineage half of criterion 9: a refusal is recorded, not merely absent.

    The mutation is a refusal that writes nothing at all. A run refused with no record is
    indistinguishable from a run nobody submitted, which loses the one thing a reader
    needs -- that the platform was asked and said no.
    """
    assert refused.body("intent") is not None
    assert refused.body("decision") is not None
    assert set(refused.bodies) == {"intent", "decision"}
    assert refused.job is None
    assert refused.logs is None


def test_exactly_one_compute_environment_backs_the_one_provisioned_profile(
    evidence: CommittedPhase3Evidence,
) -> None:
    """Criterion 15. The mutation is calling a profile backed because a template names it.

    A template creating a compute environment is a request. VALID and ENABLED is the
    service agreeing, and it is a different fact -- an environment can be created and land
    INVALID, in which case every job queued to it waits forever with no error anywhere.
    """
    found = evidence.compute_environment
    assert found is not None
    assert found.compute_environment_name == COMPUTE_ENVIRONMENT
    assert found.status == "VALID"
    assert found.state == "ENABLED"
    assert found.usable
    assert found.job_queue_names == (JOB_QUEUE,)


def test_the_compute_environment_held_no_capacity_after_the_runs_finished(
    evidence: CommittedPhase3Evidence,
) -> None:
    """Criterion 16. The mutation is calling the environment idle on one number.

    minvCpus is what the template asks for and is asserted from the template elsewhere. The
    two numbers below are what the account is doing, and BOTH are needed, which this test
    said for a long time about only the first.

    Measured on 2026-07-28 against the first GPU run. The job reached SUCCEEDED at
    22:33:48Z. desiredvCpus read zero by 22:34:47Z, under a minute later. The g5.xlarge it
    had started went on running until 22:41:5xZ -- seven minutes during which this test's
    original assertion was satisfied and an instance was on the bill.
    ecs:list-container-instances read zero across the same window too, because the agent
    deregisters before the host goes away.

    So desiredvCpus answers what the scheduler is asking for, and neither it nor the ECS
    view answers what is being paid for. live_instance_count is the one that does, and it
    is attributed by the auto scaling group tag Batch puts on its own instances rather than
    by an instance type, so it cannot be satisfied by counting the wrong shapes.
    """
    found = evidence.compute_environment
    assert found is not None
    assert found.minimum_vcpus == 0
    assert found.desired_vcpus == 0
    assert found.live_instance_count == 0
    assert found.idle_and_holding_nothing
    assert found.maximum_vcpus > 0


def test_an_environment_at_zero_desired_with_an_instance_still_up_is_not_idle(
    evidence: CommittedPhase3Evidence,
) -> None:
    """The mutation is putting ``desired_vcpus == 0`` back as the whole of the idle claim.

    This is the state the account was actually in for seven minutes after the first GPU
    run, reconstructed from the committed record so it can be asserted rather than
    remembered. Every other field is the real one; only the instance count is the value the
    account had at 22:35Z.

    A reader that took the first number alone would call this idle. It is a g5.xlarge at
    $1.006/hour with an empty queue, which is precisely what the criterion exists to
    refuse.
    """
    found = evidence.compute_environment
    assert found is not None

    still_billing = found.model_copy(update={"live_instance_count": 1})

    assert still_billing.desired_vcpus == 0
    assert still_billing.usable
    assert not still_billing.idle_and_holding_nothing


def test_every_lineage_object_carries_an_s3_attested_checksum_and_version(
    evidence: CommittedPhase3Evidence,
) -> None:
    """Criterion 17. The mutation is asserting the writer asked for a checksum.

    Every writer in this platform sends ChecksumAlgorithm, and a test over the ASL proves
    it. Whether S3 computed and stored one is a fact about the object, readable only from
    HeadObject with checksum mode enabled. The two are different claims and the second is
    the criterion.
    """
    seen = 0
    for run in evidence.runs:
        assert run.lineage is not None
        assert run.lineage.every_object_is_attested
        for record in run.lineage.objects:
            assert record.checksum_sha256, record.key
            assert record.version_id, record.key
            assert record.content_length > 0, record.key
            seen += 1
    assert seen >= 9


def test_one_run_id_resolves_to_all_eleven_artifacts(
    succeeded: CommittedPhase3Run,
) -> None:
    """Criterion 19, the gate restated as an assertion.

    The mutation this exists to catch is another criterion passing for the wrong reason:
    each of the others reads one or two artifacts, and any of them could be satisfied by a
    record that agrees with itself and with nothing else. This is the only check that
    fails when the eleven do not all resolve from the same run id and agree.

    Named rather than counted. A test asserting ``len(artifacts) == 11`` would go on
    passing after somebody removed one and added another.
    """
    resolved = succeeded.artifacts
    assert set(resolved) == set(TRACEABLE_ARTIFACTS)
    assert succeeded.unresolved_artifacts == ()
    assert succeeded.traceable
    for name in TRACEABLE_ARTIFACTS:
        assert resolved[name], name


def test_the_session_that_started_the_run_came_through_the_approval_gate(
    succeeded: CommittedPhase3Run,
) -> None:
    """One of the eleven, asserted on its content rather than its presence.

    The mutation is capturing whichever session happened to be most recent. Every
    submission assumes the same role, so a capture that took the latest one would name a
    session belonging to a different run and still look complete. What makes this session
    this run's is that it is the one whose StartExecution named this run id, and what
    makes it the gate's is the environment in its subject claim.
    """
    session = succeeded.session
    assert session is not None
    assert session.oidc_issuer == "token.actions.githubusercontent.com"
    assert session.oidc_audience == "sts.amazonaws.com"
    assert session.oidc_subject.startswith("repo:")
    assert ":environment:run-approval-" in session.oidc_subject
    assert session.assumed_at < session.expires_at
    assert session.role_name.startswith("sbsandbox-intern-edullm-admission")


def test_the_admission_execution_is_named_for_the_run_it_admitted(
    succeeded: CommittedPhase3Run,
) -> None:
    """The execution name is the run id, which is what makes a duplicate refusable."""
    execution = succeeded.execution
    assert execution is not None
    assert execution.name == succeeded.run_id
    assert execution.status == "SUCCEEDED"
    assert execution.error is None


def test_the_networking_the_compute_environment_uses_is_recorded(
    evidence: CommittedPhase3Evidence,
) -> None:
    """Criterion 21. The mutation is recording what the template asks for.

    A stack applied from a laptop can land somewhere other than where its template says,
    and a record copied from the template would agree with itself forever. These ids come
    from the deployed environment, so a reader can reconstruct the placement without
    opening a console.
    """
    found = evidence.compute_environment
    assert found is not None
    assert found.vpc_id.startswith("vpc-")
    assert found.subnet_ids
    assert all(subnet.startswith("subnet-") for subnet in found.subnet_ids)
    assert found.security_group_ids
    assert all(group.startswith("sg-") for group in found.security_group_ids)
    # Every subnet is in the one VPC, which the capture refuses to record otherwise.
    assert len(set(found.subnet_ids)) == len(found.subnet_ids)


def test_the_bindings_written_before_the_asl_fix_are_recorded_as_permanently_corrupt(
    evidence: CommittedPhase3Evidence,
) -> None:
    """The limitation, asserted rather than described in prose somewhere.

    The mutation this exists to catch is a later capture quietly dropping the unloadable
    objects and making the store look uniform. They are attested, versioned and intact;
    what they are not is loadable, and a run holding one is not traceable end to end. The
    lineage store is write-once, so this can never be repaired -- only recorded.
    """
    unloadable = [
        record
        for run in evidence.runs
        if run.lineage is not None
        for record in run.lineage.unloadable
    ]
    assert unloadable, (
        "no committed capture records an unloadable object; if the corrupt bindings are no "
        "longer captured, this limitation has stopped being visible rather than stopped "
        "being true"
    )
    for record in unloadable:
        assert record.record_kind == "binding"
        # Attested and versioned all the same: the object arrived intact and is simply
        # not the shape a binding has to be.
        assert record.checksum_sha256
        assert record.version_id
        assert not record.loads_as_contract
    # And the run holding one says so, rather than reporting a full chain.
    corrupt_runs = [
        run
        for run in evidence.runs
        if run.lineage is not None and run.lineage.unloadable
    ]
    for run in corrupt_runs:
        assert not run.traceable
        assert "binding" in run.unresolved_artifacts


def test_the_four_deployed_roles_match_the_templates_that_declare_them() -> None:
    """The deployed half of criteria 13 and 14, which no template test can supply.

    The mutation this exists to catch is a role widened in the console. A template test
    goes on passing forever after that, because the template is what it reads; only a
    capture of the deployed role compared against the template can see it.
    """
    captures = read_committed_role_captures(
        PROJECT_ROOT,
        capture_dir=CAPTURES / "roles",
        role_templates=PHASE3_ROLE_TEMPLATES,
    )
    assert len(captures) == len(PHASE3_ROLE_TEMPLATES)
    for capture in captures:
        assert capture.verdict is CaptureVerdict.OK, (capture.role_name, capture.detail)
        assert capture.report is not None
        assert capture.report.matches


def test_no_committed_capture_carries_an_account_id() -> None:
    """What is committed here is reviewable by anybody, not merely loadable.

    The mutation is a capture tool that stops redacting. The contracts refuse an account
    id on load, so this is the same claim made against the bytes on disk -- including the
    lineage bodies, which are committed verbatim and do not go through a contract.

    Digests are removed before the search, and that is not a loosening. A manifest digest
    is sixty-four hexadecimal characters and roughly one in six contains twelve
    consecutive decimal ones, so searching the raw text reports digests as account ids --
    an alarm that fires on the evidence being valid, which is the kind people learn to
    silence. The companion case below is what keeps the check honest.
    """
    for path in sorted(CAPTURES.rglob("*.json")):
        text = redact_content_digests(path.read_text(encoding="utf-8"))
        assert not AWS_ACCOUNT_ID_PATTERN.search(text), path


def test_the_account_id_check_still_catches_an_account_id_outside_a_digest(
    tmp_path: Path,
) -> None:
    """The other half of the check above, because a check that cannot fail proves nothing.

    Stripping digests before searching is only safe if what remains is still searched.
    This is a committed capture with an account id in an ARN -- the exact shape a
    capture tool that stopped redacting would produce -- and it has to be found.
    """
    directory = copy_captures(tmp_path / "captures")
    leaked = directory / RUNS_SUBDIR / "leak.json"
    leaked.write_text(
        '{"arn": "arn:aws:batch:us-east-1:123456789012:job-queue/q"}', encoding="utf-8"
    )

    offenders = [
        path
        for path in sorted(directory.rglob("*.json"))
        if AWS_ACCOUNT_ID_PATTERN.search(
            redact_content_digests(path.read_text(encoding="utf-8"))
        )
    ]

    assert offenders == [leaked]


def test_no_committed_sanitized_record_carries_a_credential() -> None:
    """The stricter scan, on the records a contract wrote rather than on lineage bodies.

    Lineage bodies are excluded deliberately and only they: a manifest digest is
    sixty-four hexadecimal characters, which a shape-based scan cannot tell from a
    credential, and masking it would destroy the evidence the record exists to carry.
    """
    for path in sorted(CAPTURES.rglob(f"*{CAPTURE_SUFFIX}")):
        masked = redact_content_digests(path.read_text(encoding="utf-8"))
        assert scan_for_secrets(masked) == masked, path


# --------------------------------------------------------------------------------------
# What happens when they stop being true
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("age", "expected"),
    [(FRESHNESS_WINDOW - ONE_MINUTE, True), (FRESHNESS_WINDOW + ONE_SECOND, False)],
    ids=["a minute inside the window", "a second outside it"],
)
def test_the_window_is_the_boundary_and_a_second_past_it_is_over(
    tmp_path: Path, age: timedelta, expected: bool
) -> None:
    """Probed a second past and a minute short rather than exactly on the boundary.

    The comparison is against the clock at load time, so an offset of exactly thirty days
    is already over by however long the test took to get there. The mutation this catches
    is somebody widening FRESHNESS_WINDOW to keep an expiring capture green.
    """
    assert aged(tmp_path / "captures", age).holds is expected


def test_an_expired_capture_says_what_to_do_rather_than_going_quiet(
    tmp_path: Path,
) -> None:
    """The mutation is an expiry that reads as a reason to re-run the job.

    The runs do not need repeating: every object is still in a write-once store. What
    expired is when somebody last looked, and the guidance has to say so or the next
    reader will spend money to renew a record.
    """
    expired = aged(tmp_path / "captures", FRESHNESS_WINDOW + ONE_MINUTE)

    assert not expired.holds
    problems = [problem for run in expired.runs for problem in run.problems]
    assert problems
    assert {problem.reason for problem in problems} >= {"evidence_stale"}
    for problem in problems:
        if problem.reason != "evidence_stale":
            continue
        assert "tools/capture_phase3_evidence.py" in problem.detail
        assert "do not need repeating" in problem.detail


def test_a_batch_job_captured_under_another_run_does_not_hold(tmp_path: Path) -> None:
    """The mutation is a capture that files a real job under the wrong run.

    Both records would be true statements. Together they would establish that a container
    ran for a run it had nothing to do with, which is the lineage error this whole
    directory exists to make impossible to commit by accident.
    """
    directory = copy_captures(tmp_path / "captures")
    run_id = "run_019fa96f-8f10-705a-a7a9-69c42eafce16"
    rewrite(
        record_path(directory, run_id, "batch-job"),
        run_id="run_019fa000-0000-7000-8000-ffffffffffff",
    )

    evidence = read_committed_phase3_evidence(PROJECT_ROOT, directory=directory)
    run = evidence.run(run_id)

    assert run is not None
    assert not run.holds
    assert "record_describes_another_run" in reasons(run)


def test_a_job_whose_name_is_not_the_run_id_does_not_hold(tmp_path: Path) -> None:
    """The mutation is losing the third join while both ends still agree.

    The run id is the S3 key, the execution name and the job name. If Batch stops holding
    it as the job name, a job and its lineage records can no longer be matched without a
    lookup table, and nothing else in the capture would notice.
    """
    directory = copy_captures(tmp_path / "captures")
    run_id = "run_019fa96f-8f10-705a-a7a9-69c42eafce16"
    rewrite(
        record_path(directory, run_id, "batch-job"),
        batch_job_name="run_019fa000-0000-7000-8000-ffffffffffff",
    )

    evidence = read_committed_phase3_evidence(PROJECT_ROOT, directory=directory)
    run = evidence.run(run_id)

    assert run is not None
    assert not run.holds
    assert "job_name_is_not_the_run_id" in reasons(run)


def test_a_log_stream_that_is_not_the_jobs_stream_does_not_hold(tmp_path: Path) -> None:
    """The mutation is a stream captured from a different container.

    Lines exist, the record loads, and the criterion about stdout would read as satisfied
    by output some other job printed.
    """
    directory = copy_captures(tmp_path / "captures")
    run_id = "run_019fa96f-8f10-705a-a7a9-69c42eafce16"
    rewrite(
        record_path(directory, run_id, "log-stream"),
        log_stream_name="cpu-run/default/0000000000000000000000000000ffff",
    )

    evidence = read_committed_phase3_evidence(PROJECT_ROOT, directory=directory)
    run = evidence.run(run_id)

    assert run is not None
    assert not run.holds
    assert "log_stream_is_not_the_jobs_stream" in reasons(run)


def test_a_stream_that_resolves_and_carries_nothing_does_not_hold(tmp_path: Path) -> None:
    """The mutation is a log configuration that creates the stream and delivers no output.

    A named stream with no lines is exactly what a broken awslogs driver produces, and the
    criterion is that stdout is available rather than that a stream exists.
    """
    directory = copy_captures(tmp_path / "captures")
    run_id = "run_019fa96f-8f10-705a-a7a9-69c42eafce16"
    rewrite(record_path(directory, run_id, "log-stream"), lines=[])

    evidence = read_committed_phase3_evidence(PROJECT_ROOT, directory=directory)
    run = evidence.run(run_id)

    assert run is not None
    assert not run.holds
    assert "log_stream_carried_no_output" in reasons(run)


def test_an_attempt_naming_another_job_does_not_hold(tmp_path: Path) -> None:
    """The mutation is the middle link of criterion 3's chain pointing elsewhere.

    The result still names the attempt and the job still carries the run id, so a check of
    the two ends passes while the record describes somebody else's container.
    """
    directory = copy_captures(tmp_path / "captures")
    run_id = "run_019fa96f-8f10-705a-a7a9-69c42eafce16"
    attempts = sorted(
        (directory / RUNS_SUBDIR / run_id / "records" / "attempt").rglob("*.json")
    )
    assert len(attempts) == 1
    rewrite(attempts[0], scheduler_job_id="00000000-0000-4000-8000-ffffffffffff")

    evidence = read_committed_phase3_evidence(PROJECT_ROOT, directory=directory)
    run = evidence.run(run_id)

    assert run is not None
    assert not run.holds
    assert "attempt_names_another_job" in reasons(run)


def test_a_refusal_whose_run_started_a_job_anyway_does_not_hold(tmp_path: Path) -> None:
    """The failure criterion 9 exists to find, rather than a defect in the capture.

    The mutation is a platform that records a refusal and submits regardless. Everything
    else about the record would look correct: the decision says no, the execution failed,
    and a job is running that nobody approved.
    """
    directory = copy_captures(tmp_path / "captures")
    run_id = "run_019fa984-085c-7088-9c94-799e4b5d9126"
    rewrite(
        record_path(directory, run_id, "refusal"),
        matching_batch_job_ids=["00000000-0000-4000-8000-ffffffffffff"],
    )

    evidence = read_committed_phase3_evidence(PROJECT_ROOT, directory=directory)
    run = evidence.run(run_id)

    assert run is not None
    assert not run.holds
    assert "refused_run_started_a_job" in reasons(run)


def test_a_refusal_that_searched_only_some_statuses_does_not_hold(
    tmp_path: Path,
) -> None:
    """The mutation is establishing an absence without saying where you looked.

    ListJobs answers one status at a time. A search that skipped RUNNABLE would miss
    precisely the case where a refused submission is sitting in the queue waiting for
    capacity, and would report the same empty list as a search that covered everything.
    """
    directory = copy_captures(tmp_path / "captures")
    run_id = "run_019fa984-085c-7088-9c94-799e4b5d9126"
    rewrite(
        record_path(directory, run_id, "refusal"),
        searched_job_statuses=["SUCCEEDED", "FAILED"],
    )

    evidence = read_committed_phase3_evidence(PROJECT_ROOT, directory=directory)
    run = evidence.run(run_id)

    assert run is not None
    assert not run.holds
    assert "absence_established_nowhere" in reasons(run)


def test_a_body_removed_from_an_object_that_loads_is_reported_as_absent(
    tmp_path: Path,
) -> None:
    """The mutation is deleting an inconvenient record and leaving the attestation.

    A withheld body and a deleted one look identical on disk. What separates them is the
    attestation: a body may be missing only for an object the attestation says does not
    load, and anything else is evidence somebody removed.
    """
    directory = copy_captures(tmp_path / "captures")
    run_id = "run_019fa96f-8f10-705a-a7a9-69c42eafce16"
    results = sorted(
        (directory / RUNS_SUBDIR / run_id / "records" / "result").rglob("*.json")
    )
    assert len(results) == 1
    results[0].unlink()

    evidence = read_committed_phase3_evidence(PROJECT_ROOT, directory=directory)
    run = evidence.run(run_id)

    assert run is not None
    assert not run.holds
    assert CaptureLoadVerdict.ABSENT.value in reasons(run)
    assert "result" in run.unresolved_artifacts


def test_a_directory_with_no_records_reports_every_one_as_absent(tmp_path: Path) -> None:
    """A capture nobody took must not read like a run that produced nothing."""
    empty = tmp_path / "nothing"
    (empty / RUNS_SUBDIR / "run_019fa000-0000-7000-8000-ffffffffffff").mkdir(parents=True)

    evidence = read_committed_phase3_evidence(PROJECT_ROOT, directory=empty)

    assert not evidence.holds
    assert len(evidence.runs) == 1
    assert reasons(evidence.runs[0]) == {CaptureLoadVerdict.ABSENT.value}
    assert evidence.runs[0].job is None
    assert evidence.compute_environment is None


def test_no_committed_run_at_all_is_reported_rather_than_read_as_nothing_to_prove(
    tmp_path: Path,
) -> None:
    """The mutation is an empty fixtures directory passing every criterion vacuously.

    With no runs committed, every loop over runs above iterates zero times and asserts
    nothing. This is the check that turns that into a failure.
    """
    empty = tmp_path / "nothing"
    empty.mkdir()

    evidence = read_committed_phase3_evidence(PROJECT_ROOT, directory=empty)

    assert not evidence.holds
    assert evidence.runs == ()
    assert {problem.reason for problem in evidence.problems} == {
        CaptureLoadVerdict.ABSENT.value
    }
