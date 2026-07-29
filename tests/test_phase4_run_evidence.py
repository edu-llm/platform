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

    assert len(runs) == 3, "three GPU jobs ran and all three are committed"
    assert all(run.job.run_id == run.run_id for run in runs), (
        "the directory a record sits in and the run id inside it must be the same run"
    )


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

    Mutation: widen the role to ``teams/*/runs/*``. It was that, and one team exists, so
    the widening changes nothing anybody can see today and makes the cross-team check
    impossible to close honestly tomorrow.
    """
    run = training_run()
    scope = role_scope()
    key = f"teams/{TEAM}/runs/{run.run_id}/checkpoints/step-20/model.pt"

    assert scope.may_reach(key)
    assert not scope.may_reach(f"teams/other-team/runs/{run.run_id}/anything")


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


def test_the_three_runs_used_three_images_and_the_last_one_is_what_is_deployed() -> None:
    """Reads BOTH sides. Mutation: assert the three runs agree, or read only the template.

    They do not agree, and that is the true history rather than a defect: the image was
    rebuilt twice between the first probe and the training run, once because the published
    image installed nothing and could not import torch, and once because the torch it then
    pinned did not support a keyword OLMo-core calls. A test asserting one digest across
    all three would have to be satisfied by throwing away two thirds of the evidence.

    What must hold is the narrower thing: the run that trained ran the digest the deployed
    definition still names. Read from the template, so a re-pin without a re-run fails here
    rather than at the next submission.
    """
    template = (
        Path(__file__).resolve().parents[1] / "infra" / "batch-compute-gpu.yaml"
    ).read_text()
    runs = captured_runs()

    assert len({run.job.image_digest for run in runs}) == 3
    assert training_run().job.image_digest in template
    # One definition, three revisions, in the order the images were pinned. Batch registers
    # a new revision for every change, so the suffix is the account's own record of how many
    # times the image moved -- and it agreeing with the digest count is what says each of
    # those moves was a deliberate re-pin rather than a tag quietly resolving somewhere else.
    assert [run.job.job_definition_name for run in runs] == [
        "sbsandbox-intern-edullm-gpu-run:1",
        "sbsandbox-intern-edullm-gpu-run:2",
        "sbsandbox-intern-edullm-gpu-run:3",
    ]
