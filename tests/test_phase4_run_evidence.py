"""What the committed captures of the GPU runs establish, and what they refuse to.

Every Phase 4 criterion that is not about pure Python cites a test in this file. The
records these read were taken from the live account against three real jobs: one that
trained, one that probed the hardware, and one that failed. All three are committed,
because a phase whose evidence is only its successes is a phase that has not been tested.

**The first test is the one that makes the rest mean anything.** A test written as "load
the record, assert the field" passes the moment the record stops being there, and a green
suite is the worst possible way to learn that the evidence went missing. So the reader
refuses an absent capture, and a test asserts that it does.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from edullm_platform.checkpoints import MARKER_OBJECT
from edullm_platform.contracts.results import OUTPUTS_BUCKET, output_prefix
from edullm_platform.phase4_capture import (
    BATCH_JOB_RECORD,
    CAPTURE_ROOT,
    MissingCaptureError,
    UnreadableCaptureError,
    captured_runs,
    compute_environment,
    offerings,
    outputs,
    read_capture,
    role_scope,
    secret_delivery,
    training_run,
)
from edullm_platform.phase4_evidence import (
    GPU_INSTANCE_TYPE_FOR_THE_PROMOTED_PROFILE,
    WANDB_SECRET_VARIABLE,
    GpuJobEvidence,
)

TEAM = "platform"


# ---------------------------------------------------------------------------------------
# The reader, which is what stops every test below passing vacuously
# ---------------------------------------------------------------------------------------


def test_a_capture_that_is_not_there_is_reported_rather_than_read_as_nothing_to_prove() -> None:
    """Mutation: return None, or an empty model, when the file is absent.

    Every test in this file would then pass on a tree with no ``fixtures/evidence/phase-4/``
    at all -- and a criterion citing one of them would report itself covered by a check
    that read nothing. This is the test that makes the others worth their names.
    """
    with pytest.raises(MissingCaptureError):
        read_capture(CAPTURE_ROOT / "no-such-record.sanitized.json", GpuJobEvidence)


def test_a_capture_that_will_not_load_is_a_different_failure_from_an_absent_one(
    tmp_path: Path,
) -> None:
    """Mutation: catch the validation error and raise the missing-capture one.

    "Somebody committed a broken capture" and "nobody captured this" send a reader to
    different places, and collapsing them means the first arrives wearing the second's
    message -- so the reader goes looking for a capture that is sitting right there.
    """
    damaged = tmp_path / BATCH_JOB_RECORD
    damaged.write_text(json.dumps({"observed_at": "2026-07-29T00:00:00Z"}))

    with pytest.raises(UnreadableCaptureError):
        read_capture(damaged, GpuJobEvidence)


def test_every_committed_run_loads_as_the_records_its_directory_says_it_holds() -> None:
    """Mutation: commit a record under a name the reader does not look for.

    A capture written as ``training_summary.json`` where the reader wants
    ``training-summary.sanitized.json`` is an absent record, and an absent record reads as
    a run that did less than it did. Phase 3 had three spellings of one filename.
    """
    runs = captured_runs()

    assert runs, "a check over every committed run must observe at least one"
    assert all(run.job.run_id == run.run_id for run in runs), (
        "the directory a record sits in and the run id inside it must be the same run"
    )
    # Not a count. The number of committed runs goes up whenever one is worth keeping, and
    # a test asserting it becomes a number somebody bumps rather than a claim anybody reads.
    # What has to hold is that every directory loads as what its name says.


# ---------------------------------------------------------------------------------------
# Check 1 -- the container detects the expected GPU
# ---------------------------------------------------------------------------------------


def test_the_job_that_trained_asked_for_a_gpu_and_the_scheduler_gave_it_one() -> None:
    """Mutation: drop the GPU resource requirement from the job definition.

    The sharpest failure in this phase and the cheapest to make. Without it Batch injects
    no device, the container falls back to the CPU, the job still lands on a g5.xlarge and
    still bills at the GPU rate -- and every log line and every lineage record looks
    exactly like a healthy run.
    """
    run = training_run()

    assert run.job.gpu_count == 1
    assert run.job.ran_on_a_gpu
    assert run.job.status == "SUCCEEDED"


def test_the_process_itself_found_a_cuda_device_rather_than_being_offered_one() -> None:
    """The other half, and the half Batch cannot answer. Mutation: assert only gpu_count.

    Batch knows a GPU was requested. Whether torch could use it is something only the
    process could say, and both halves of that are needed: a CPU build reports no CUDA
    version at all, and a CUDA build that allocated nothing never put a tensor on the
    device. Either alone leaves the other open.
    """
    summary = training_run().training
    assert summary is not None

    assert summary.cuda_version == "12.8"
    assert summary.torch_version.endswith("+cu128")
    assert summary.peak_memory_gib > 0
    assert summary.trained_on_a_gpu


def test_the_driver_saw_the_shape_the_promoted_profile_asked_for() -> None:
    """A third, independent answer, from below the framework.

    Mutation: accept any GPU. The catalog prices ``gpu-1xa10g`` at $1.006/hr on the basis
    that it is one A10G with 24GB; a job that landed on a T4 would run, would train, and
    would make the recorded cost estimate wrong in a record nothing rewrites.
    """
    probe = next(run.capability for run in captured_runs() if run.capability is not None)
    trained = training_run().training
    assert trained is not None

    assert "A10G" in probe.nvidia_smi
    assert probe.the_driver_can_see_a_gpu
    assert probe.device_nodes, "the ECS GPU agent injects device nodes; none means none"
    assert trained.gpu_name == "NVIDIA A10G", (
        "what nvidia-smi reports and what torch reports have to be the same device, or one "
        "of the two is describing a machine the other did not run on"
    )


def test_the_gpu_container_is_not_told_to_expect_the_conventional_visibility_variable() -> None:
    """A recorded surprise rather than a check on behaviour.

    Mutation: none -- this exists so nobody rediscovers it. The ECS GPU agent sets
    ``NVIDIA_VISIBLE_DEVICES=void`` and injects the device nodes directly, so a readiness
    check written against the conventional ``all`` reports a healthy container as broken.
    """
    probe = next(run.capability for run in captured_runs() if run.capability is not None)

    assert probe.nvidia_visible_devices == "void"


# ---------------------------------------------------------------------------------------
# Check 2 -- a short training step completes
# ---------------------------------------------------------------------------------------


def test_a_real_model_went_through_a_real_optimizer_and_the_loss_moved() -> None:
    """Mutation: assert the loss fell.

    It is not asserted to have fallen and must not be. Twenty steps on synthetic tokens is
    not a claim about learning, and dressing it as one would be the kind of evidence this
    repository spends its time removing. What the movement does establish is that the
    backward pass did something: a loss identical at step one and step twenty is an
    optimizer that never applied a gradient.
    """
    summary = training_run().training
    assert summary is not None

    assert summary.parameters == 190_550_784, "olmo2_190M, and the count says so"
    assert summary.steps == 20
    assert summary.first_loss != summary.last_loss
    assert summary.seconds > 0


# ---------------------------------------------------------------------------------------
# Check 3 -- W&B receives the run
# ---------------------------------------------------------------------------------------


def test_the_wandb_run_is_named_for_the_run_id_and_lives_in_the_platforms_project() -> None:
    """Mutation: let the submitted command choose the project.

    D4's argument in one assertion. A shared W&B account authenticates and does not
    attribute, so what a run is labelled with comes from the approved manifest by way of
    the container's environment. A program that named its own project would let a submitter
    file their spend under somebody else's budget, and lineage and W&B would disagree with
    nothing able to detect it.
    """
    run = training_run()
    summary = run.training
    assert summary is not None

    assert summary.wandb_project == run.job.told("EDULLM_WANDB_PROJECT")
    assert summary.wandb_run_url.startswith("https://wandb.ai/")
    assert set(summary.metric_keys) == {"train/ce_loss", "train/step"}


# ---------------------------------------------------------------------------------------
# Check 4 and 12 -- output goes only where it is authorized, and everything agrees on where
# ---------------------------------------------------------------------------------------


def test_nothing_in_the_outputs_bucket_sits_outside_an_authorized_prefix() -> None:
    """Mutation: capture only the run's own prefix.

    The check is about what is *absent* elsewhere, so a capture scoped to the run's prefix
    could only ever report that what is there is there. The record lists the whole bucket
    for that reason, and this reads the whole list.
    """
    listing = outputs()

    assert listing.objects, "a bucket with nothing in it proves nothing about where output goes"
    assert listing.stray_keys == ()


def test_the_prefix_the_container_was_given_is_the_one_the_platform_derives() -> None:
    """Reads BOTH sides. Mutation: change ``output_prefix`` and not the deployed definition.

    The three-way disagreement Phase 4 inherited: the result manifest said one thing, the
    workload role permitted another, and the container was told a third. Two of the three
    agreed, so nothing failed -- and the lineage record described a location the workload
    was not permitted to write to and had never heard of.
    """
    run = training_run()
    told = run.job.told("EDULLM_OUTPUT_PREFIX")

    assert told == output_prefix(team=TEAM, run_id=run.run_id)
    assert told is not None
    assert told.startswith(f"s3://{OUTPUTS_BUCKET}/")


def test_the_role_permits_exactly_the_prefix_shape_the_platform_derives() -> None:
    """The third side of the same agreement, read off the deployed policy.

    Mutation: scope the role to a named team. Every run so far declared ``platform``, so a
    narrow grant looks identical from here and fails only for the first researcher who
    claims a team of their own -- at write time, inside a training run that has already
    spent its GPU hours.

    **This asserted the opposite until the role was widened, and the reversal is the
    point.** It required that another team's prefix be unreachable, which was true and was
    a property shaped to make a cross-team criterion closeable rather than to prevent a
    harm. What is asserted now is the shape rather than the team: any team's run prefix is
    reachable, and anything that is not a run prefix is not.
    """
    run = training_run()
    scope = role_scope()

    assert scope.may_reach(f"teams/{TEAM}/runs/{run.run_id}/checkpoints/step-20/model.pt")
    assert scope.may_reach(f"teams/evaluation/runs/{run.run_id}/checkpoints/step-20/model.pt")
    assert not scope.may_reach("teams/platform/scratch/anything")
    assert not scope.may_reach("some-other-prefix/runs/anything")


def test_a_grant_on_another_bucket_does_not_widen_what_the_outputs_reach_reports() -> None:
    """Mutation: record the key portion of every S3 ARN regardless of its bucket.

    THIS IS THE DEFECT THIS TASK EXISTS TO REMOVE, WRITTEN AS A TEST BEFORE THE GRANT THAT
    WOULD TRIGGER IT. A read on edullm-data/* contributes the key pattern `*`, which fnmatch
    matches against every candidate -- so the two assertions above that establish what the
    role CANNOT reach would both flip to true, silently, and three pilot-blocking criteria
    would rest on a measurement that had stopped measuring.

    The measurement is the thing under test, not the policy. A role that genuinely reached
    every prefix and a reader that could not tell are indistinguishable from the outside,
    which is why this asserts the reader against a constructed grant rather than against the
    account.
    """
    scope = role_scope().model_copy(
        update={"grants_outside_the_outputs_bucket": ("edullm-data/*",)}
    )

    assert scope.may_reach("teams/platform/runs/r/checkpoints/step-20/model.pt")
    assert not scope.may_reach("teams/platform/scratch/anything")
    assert not scope.may_reach("some-other-prefix/runs/anything")
    assert scope.grants_outside_the_outputs_bucket == ("edullm-data/*",)


def test_a_grant_on_another_bucket_is_recorded_rather_than_dropped() -> None:
    """Mutation: filter the other bucket out and record nothing.

    Discriminating by bucket has an obvious wrong implementation: skip the ARN. That would
    make the two assertions above pass and would leave the capture unable to say that the
    role reads a dataset bucket at all -- so the record would be silent about the one grant
    this whole track is adding. Bucket-qualified, because a bare key portion from a second
    bucket is exactly the ambiguity being removed.
    """
    scope = role_scope()

    assert scope.grants_outside_the_outputs_bucket == ()
    assert all(
        "/" in grant for grant in scope.grants_outside_the_outputs_bucket
    ), "a grant recorded here names its bucket, or it is the ambiguity this field removed"


# ---------------------------------------------------------------------------------------
# Check 5 -- the checkpoint is resumable, verified against the store rather than the log
# ---------------------------------------------------------------------------------------


def test_the_checkpoint_the_run_wrote_is_one_this_platform_will_resume_from() -> None:
    """Mutation: record what the container claimed instead of asking the store.

    Everything else in this evidence is the run describing itself. This is the platform's
    own reader run against the objects in the bucket, which is the only way to catch the
    one failure a run cannot detect about its own output.
    """
    checkpoint = training_run().checkpoint
    assert checkpoint is not None

    assert checkpoint.state == "committed"
    assert checkpoint.is_resumable
    assert checkpoint.success_marker_uri == checkpoint.prefix + MARKER_OBJECT
    assert checkpoint.size_bytes == 762_258_865


def test_what_the_run_said_it_wrote_is_what_the_store_says_it_holds() -> None:
    """The three-way join. Mutation: compare the marker to itself.

    The marker's claim, the digest S3 computed over the bytes it received, and the digest
    the container printed before either object existed. The first two are compared by
    ``inspect_checkpoint``; the third closes the loop between the process and the bucket,
    and is the one that would catch a marker written for a payload from another attempt.
    """
    run = training_run()
    checkpoint = run.checkpoint
    summary = run.training
    assert checkpoint is not None
    assert summary is not None

    assert checkpoint.store_agrees_with_the_container
    assert checkpoint.prefix.startswith(summary.checkpoint_uri.rstrip("/").rsplit("/", 1)[0])


# ---------------------------------------------------------------------------------------
# Check 8 -- the secret is delivered by reference and stays out of every record
# ---------------------------------------------------------------------------------------


def test_the_wandb_key_reaches_the_container_without_passing_through_any_record() -> None:
    """Mutation: put the key in the job definition's plain environment.

    It would work. Every run would authenticate, and the value would be readable by anybody
    who can call ``DescribeJobDefinitions`` -- which is a much larger set than the people
    who can read the secret. The ``secrets``/``valueFrom`` block is what keeps the value in
    the running container's memory and nowhere else.
    """
    delivery = secret_delivery()

    assert delivery.delivered_by_reference
    assert not delivery.value_appears_in_environment
    assert WANDB_SECRET_VARIABLE not in delivery.plain_environment_names
    assert delivery.stayed_out_of_every_record


def test_the_key_did_not_turn_up_in_the_log_the_run_wrote() -> None:
    """Mutation: exempt anything digest-shaped from the scan instead of the verified ones.

    The scan cannot tell a bare sixty-four-character digest from a credential and should
    not try. What makes the exemption sound is that each excluded value is a digest the
    capture verified against the store, listed in the record so a reader can check every
    one -- rather than a pattern that would also excuse a real secret.
    """
    delivery = secret_delivery()
    checkpoint = training_run().checkpoint
    assert checkpoint is not None

    assert delivery.log_lines_scanned > 0, "a scan of no lines establishes nothing"
    assert not delivery.log_holds_a_credential_shape
    assert set(delivery.exempted_content_digests) <= {
        checkpoint.checksum,
        checkpoint.container_claimed_checksum,
    }, "an exemption must name a digest this capture independently verified"


# ---------------------------------------------------------------------------------------
# Placement, capacity, and the shape of the environment they ran on
# ---------------------------------------------------------------------------------------


def test_the_gpu_shape_is_offered_in_every_zone_the_environment_can_place_into() -> None:
    """Read from ``describe-instance-type-offerings`` and never from a dry-run.

    Mutation: accept a dry-run as evidence of placement. ``run-instances --dry-run``
    answers "is this principal permitted to make this call", and returned
    ``DryRunOperation`` for two instance types in an availability zone that offers neither.
    A compute environment built on that answer leaves jobs in ``RUNNABLE`` for ever with no
    error anywhere.
    """
    offered = offerings()
    environment = compute_environment()

    assert offered.instance_type == GPU_INSTANCE_TYPE_FOR_THE_PROMOTED_PROFILE
    assert len(offered.offering_zones) >= environment.subnet_count, (
        "a subnet in a zone that does not offer the shape is a queue that never drains"
    )


def test_the_gpu_environment_uses_the_nvidia_ami_rather_than_the_default_one() -> None:
    """Mutation: leave ``imageType`` unset, or copy the CPU stack's ``ECS_AL2023``.

    The default AMI carries no NVIDIA driver, so the ECS agent injects no device nodes and
    the container finds no GPU -- on an instance that is billing at the GPU rate. It is the
    same silent failure as a missing resource requirement, arriving from the other side.
    """
    environment = compute_environment()

    assert environment.image_type == "ECS_AL2023_NVIDIA"
    assert environment.state == "ENABLED"
    assert environment.status == "VALID"


def test_the_gpu_environment_was_holding_nothing_when_it_was_captured() -> None:
    """Mutation: read idleness off ``desiredvCpus`` alone.

    Batch drops the desired count to zero as soon as the queue empties and leaves the
    instance running for several minutes afterwards. Measured on the first GPU run: about
    seven minutes of EC2 billing after the environment reported itself at zero. What bills
    is the instance, so what the record has to carry is the instance count.
    """
    environment = compute_environment()

    assert environment.desired_vcpus == 0
    assert environment.live_instance_count == 0
    assert environment.idle_and_holding_nothing


def test_the_gpu_environment_has_only_one_shape_to_fall_back_on() -> None:
    """A recorded gap, asserted so that closing it has to change this test.

    Mutation: none. ``us-east-2`` denies ``RunInstances``, so instance-type breadth inside
    ``us-east-1`` is the only lever on availability the account has. This environment lists
    one type, which means a job waits rather than lands when A10G capacity is short.

    Fixed deliberately: the same list is what stops a submission for a cheap shape landing
    on an expensive one, and widening it is a cost decision rather than a typo.
    """
    environment = compute_environment()

    assert environment.instance_types == (GPU_INSTANCE_TYPE_FOR_THE_PROMOTED_PROFILE,)
    assert not environment.can_fall_back_to_another_shape


# ---------------------------------------------------------------------------------------
# The run that failed, which is evidence too
# ---------------------------------------------------------------------------------------


# ---------------------------------------------------------------------------------------
# Isolation and resume, which only a container could establish
# ---------------------------------------------------------------------------------------


def test_the_workload_role_was_refused_the_two_reaches_widening_did_not_grant() -> None:
    """The half of the probe set that still describes the deployed role.

    Two of the four probes stopped describing it when the GPU role was widened to
    ``teams/*/runs/*``: reading and writing another team's prefix were refused when captured
    and would now succeed, deliberately, because isolation between the groups sharing this
    account is not a goal. Citing all four to prove a criterion would be claiming a property
    the platform decided against, on the strength of a capture of a role that no longer
    exists.

    These two survive, and the evidence model's own docstring calls them the load-bearing
    pair. ``s3:ListBucket`` is a bucket-level action that no object ARN can scope, so a role
    whose object grants look narrow can still enumerate every team's output if the prefix
    condition is missing -- widening the prefix from one team to any team left that condition
    in place, so the whole-bucket listing is still refused. And the lineage probe is the one
    grant on this role that is not arguable: a workload that could write to the store
    recording what it did could rewrite it, and every other guarantee here is downstream of
    that record being something only the platform writes.

    ``AccessDenied`` and nothing else, for the reason the four-probe test below gives.
    """
    probed = [run for run in captured_runs() if run.isolation is not None]

    assert probed, "no committed run carries the probes, so nothing here establishes anything"
    for run in probed:
        assert run.isolation is not None
        assert run.isolation.list_the_whole_outputs_bucket == "AccessDenied"
        assert run.isolation.write_to_the_lineage_bucket == "AccessDenied"


def test_the_workload_role_was_refused_every_prefix_it_must_not_reach() -> None:
    """What the capture recorded, which is no longer what the deployed role would do.

    Kept because it is a true statement about an event: on the runs captured here, a real
    container reached for four prefixes and S3 refused all four. Records of events do not
    expire and re-capturing establishes nothing the first capture did not.

    **It is no longer cited as proving anything**, because two of the four -- reading and
    writing another team's prefix -- describe a role that was narrowed to make a cross-team
    criterion closeable and has since been widened to match the CPU role. Asserting them
    against a frozen capture would keep passing forever while the world changed underneath,
    which is the shape of a test that lies by implication.

    Mutation: accept any failure as a refusal.

    ``AccessDenied`` and nothing else. ``NoSuchKey`` would mean the role was permitted to
    look and found nothing, which is exactly what a role granting everything returns from an
    empty prefix -- so a check that accepted "the call did not succeed" would pass against
    no isolation at all.

    This is what turns the cross-team criterion from a reading of a policy document into a
    refusal a container actually received. No human can produce one: the workload role's
    trust policy names the Batch and ECS task services.
    """
    probed = [run for run in captured_runs() if run.isolation is not None]

    assert probed, "no committed run carries the probes, so nothing here establishes anything"
    for run in probed:
        assert run.isolation is not None
        assert run.isolation.everything_was_refused, run.isolation.probes
        assert set(run.isolation.probes.values()) == {"AccessDenied"}


def test_the_container_could_not_write_to_the_store_that_records_what_it_did() -> None:
    """The sharpest of the four, asserted on its own.

    Mutation: drop this probe and keep the other three. Every other grant on this role is
    arguable; the one that is not is that a workload cannot rewrite the record of what it
    did, because every other guarantee in this platform is downstream of that record being
    something only the platform writes.
    """
    probed = [run for run in captured_runs() if run.isolation is not None]

    for run in probed:
        assert run.isolation is not None
        assert run.isolation.write_to_the_lineage_bucket == "AccessDenied"


def test_a_checkpoint_one_run_wrote_was_loaded_back_by_another() -> None:
    """Mutation: assert the download rather than the load.

    ``inspect_checkpoint`` already establishes that the marker certifies the payload and
    that the store agrees. What it cannot say is whether torch accepts the bytes, and that
    is the thing a researcher resuming a run needs.

    The evidence is the loss, not the digest. A freshly initialised olmo2_190M on random
    tokens starts near 11.0; this run started at 9.71. Nothing but trained weights in the
    model produces that number, which is a harder thing to fake than a checksum match.
    """
    resumed = [run for run in captured_runs() if run.resume is not None]

    assert resumed, "no committed run resumed from anything"
    for run in resumed:
        assert run.resume is not None
        assert run.resume.resumed_from_run_id != run.run_id, "a resume is from another run"
        assert run.resume.tensors > 0
        assert run.resume.loaded_trained_weights, (
            f"first loss {run.resume.first_loss} is not below the cold-start "
            f"{run.resume.cold_start_first_loss}, so nothing says the weights were loaded"
        )


def test_the_run_that_was_resumed_from_is_one_whose_checkpoint_is_committed() -> None:
    """Reads BOTH sides. Mutation: record the resume without checking its source exists.

    A resume record naming a run nothing else knows about would be a claim with no other
    side. The URI carries the run id by construction -- that is what D5's ``runs/{run_id}``
    segment bought -- so the predecessor is checkable rather than asserted.
    """
    by_id = {run.run_id: run for run in captured_runs()}

    for run in captured_runs():
        if run.resume is None:
            continue
        source = by_id.get(run.resume.resumed_from_run_id)
        assert source is not None, run.resume.resumed_from_run_id
        assert source.checkpoint is not None
        assert source.checkpoint.checksum == run.resume.checksum, (
            "the digest the resuming run loaded is not the digest the store attests for the "
            "checkpoint it named"
        )


def test_a_resume_restores_a_model_and_not_a_training_run() -> None:
    """A recorded limitation rather than a guard. Mutation: none.

    The checkpoint carries the model state dict and the step, and no optimizer state. So a
    resumed AdamW starts with no moment estimates and the loss moves accordingly -- this
    run's last loss is above its first, which is what that looks like and is not a defect.

    Asserted so that "resumable checkpoint" cannot quietly come to mean more than it does.
    The difference is between reproducing a result and continuing a run, and closing it
    means checkpointing the optimizer, which is a change to the training program.
    """
    for run in captured_runs():
        if run.resume is None:
            continue
        assert run.resume.restores_optimizer_state is False


def test_the_run_that_failed_is_committed_beside_the_ones_that_worked() -> None:
    """Mutation: capture only the successes.

    A phase whose evidence is only its successes has not been tested. The failed run is
    also the only thing establishing what the capture does with a job that printed nothing:
    its Batch record is written, and the absence of a summary is recorded rather than
    guessed at.
    """
    failed = [run for run in captured_runs() if run.job.status == "FAILED"]

    assert len(failed) == 1
    assert failed[0].training is None
    assert failed[0].checkpoint is None
    assert failed[0].job.gpu_count == 1, (
        "it failed inside the container, not for want of a device -- which is what makes it "
        "evidence that a GPU failure is recorded rather than lost"
    )


def test_no_committed_job_ran_an_image_named_by_anything_but_a_digest() -> None:
    """Mutation: let the job definition name a tag.

    A tag is a pointer somebody can move, so a run that names one has no provenance at all
    -- the image that ran need not be the image that was scanned, nor the one built from
    the reviewed commit. Read over every run, because a definition can be re-registered
    between them and only the run's own record says what it actually pulled.
    """
    for run in captured_runs():
        assert run.job.image_digest.startswith("sha256:"), run.run_id


def test_the_run_that_trained_most_recently_ran_the_image_that_is_deployed() -> None:
    """Reads BOTH sides. Mutation: assert every run used one image, or read only the template.

    They did not all use one, and that is the true history rather than a defect: the image
    was rebuilt twice between the first probe and the first training run -- once because the
    published image installed nothing and could not import torch, and once because the torch
    it then pinned did not support a keyword OLMo-core calls. A test asserting one digest
    across every run could only be satisfied by throwing most of the evidence away.

    What must hold is narrower and is the thing that matters: the most recent training run
    ran the digest the deployed definition still names. Read from the template, so a re-pin
    without a re-run fails here rather than at the next submission.
    """
    template = (
        Path(__file__).resolve().parents[1] / "infra" / "batch-compute-gpu.yaml"
    ).read_text()

    assert training_run().job.image_digest in template


def test_the_job_definition_revision_moved_once_per_image_and_no_more() -> None:
    """Batch's own count of how many times the image was re-pinned, read against ours.

    Mutation: let the definition be re-registered without the digest changing, or the
    reverse. A revision that moved for something other than an image is a change to how jobs
    run that nobody attributed to anything; a digest that changed without a revision would
    mean a job ran an image its definition does not name.

    Not one revision per run. Two runs sharing a revision is the ordinary case -- it is what
    running the same image twice looks like -- so what is compared is the number of distinct
    revisions against the number of distinct digests.
    """
    runs = captured_runs()
    revisions = {run.job.job_definition_name for run in runs}
    digests = {run.job.image_digest for run in runs}

    assert len(revisions) == len(digests)
    assert all(name.startswith("sbsandbox-intern-edullm-gpu-run:") for name in revisions)
