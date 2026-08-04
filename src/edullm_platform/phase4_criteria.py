"""The Phase 4 acceptance criteria and the tests that are cited for each one.

Phase 4 takes the path Phase 3 proved for a CPU container and puts a real single-node GPU
training run through it: a model on an A10G, metrics in W&B, and a checkpoint in S3 that
this platform will resume from. This module records the eleven checks the phase must
satisfy, against the contract in :mod:`edullm_platform.criteria`.

**The capability is deployed and has run.** Jobs have gone through the GPU queue: a
capability probe that reported the device nodes it was given, two training runs that put
olmo2_190M through twenty optimizer steps on tokens they generated and wrote a 762MB
checkpoint with a success marker, one that failed, and one submitted through the form that
trained for a hundred and fifty steps on a published corpus. What each left behind is
captured, sanitized and committed under ``fixtures/evidence/phase-4/``.

**Ten of the eleven come from the master plan; criterion 12 was added by this phase.** The
plan lists eleven checks and marks nine as pilot-blocking, which is the highest proportion
of any capability phase -- the reason is the hardware, because a GPU instance bills whether
or not the container is using it. One of the marked nine has since left this phase, so
eight of the plan's marked checks remain here and criterion 12 is the ninth marked. That
criterion is the prefix-agreement check, added when Phase 4 inherited a three-way
disagreement about where a run writes its output.

**There is no criterion 9, and the absence is the record rather than a mistake.** It was
capacity failure: that a job which cannot be placed is surfaced without losing the run
intent. Half of it is already true and half of it could not be closed by running anything.
AWS Batch does not fail a job it cannot place -- it leaves it ``RUNNABLE`` indefinitely --
so "surfaced" has no mechanism behind it until the queue-wait detector of criterion 10
exists, and criterion 10 is deferred until that detector is built. A criterion blocked on a
mechanism nothing here builds holds this phase's gate red for work this phase does not own,
which is exactly the state Phase 3's three cancellation criteria were in, and it went the
same way: the check is owned beside the mechanism instead, with its sentence and its number
unchanged.

What that costs is real and worth stating. This phase's gate no longer asks whether a
capacity failure gets noticed, and nothing in this module will notice if the answer stays
no. What it buys is a gate that measures what Phase 4 can be held to, and an owner for
queue capacity who is not "nobody, indefinitely".

The transfer rests on two conditions, the ones Phase 3's rested on. The criterion keeps its
text and its number, so nothing is quietly reworded into something easier to satisfy. And
what it protected is written where a reader can act on it: that a job which cannot get
capacity sits in ``RUNNABLE`` rather than failing, that no alarm notices, that the intent
record is written before Batch is reached at all so a run that never places loses nothing
but time, and who to ask and what to quote when asking.

**The second condition was met in the wrong place for a few hours, and where it is met now
is better.** It was written onto the pilot limitations page in the root ``README.md``, which
already carried the first two facts. That page then left the README on a standing decision
about what this repository publishes -- a decision that never mentioned Phase 4 -- and the
condition went with it. The paragraph is now printed on the summary every accepted
submission ends on, beside the three facts Phase 5 criterion 11 holds there, and
``tests/test_pilot_limitations.py::test_a_submitter_is_told_a_queued_run_is_waiting_rather_than_lost``
pins it. That is the difference worth carrying forward: a page can be moved by a decision
that never mentions the criterion it was paying for, and a cited node id cannot go quiet
without a test going red.

The number was left as a hole rather than closed up, and that is deliberate rather than
untidy. A criterion number is an identifier: plan documents and decisions already written
down cite Phase 4 criteria by number, so renumbering 10 down to 9 would change what every
one of those citations means without touching the sentence it appears in. The contract in
:mod:`edullm_platform.criteria` requires numbers to be unique and says nothing about them
being contiguous, which is what makes the hole possible.

**Both remaining open criteria are DEFERRED and neither is a relabelled gap, which is the
whole point of having two words.** A deferral is a decision not to do something, with a
written trigger that makes it live again; unfinished work is a gap, and recording one as the
other is how a gate goes green while nothing changes in the account.

Criterion 10 is the queue-wait detector. A queued job bills nothing, so nothing is at risk
while it waits; the trigger that makes it live again is the first time a run sits in
``RUNNABLE`` long enough for anybody to notice. It also needs a detector *built* rather than
an alarm configured -- AWS Batch publishes no CloudWatch metric for queue depth or job
state, so there is no series to threshold.

Criterion 11 is alternate instance placement, and it moved from a gap to a deferral on
2026-07-31 because what was recorded against it had always read as a decision rather than a
shortfall. The GPU compute environment lists exactly one instance type, and that same list
is what stops a submission for a cheap shape landing on an expensive one. Widening it is one
line; which shape that line names is a cost decision somebody takes deliberately, and the
trigger says what would make it worth taking. Left recorded as a gap it read as something
nobody had got round to, which invites precisely the fast fix the narrowness exists to
prevent. It is the one re-cut in this phase that is a judgement rather than a relocation,
so the reason carries its own argument and the prices are in it.

**A criterion cites a test, never an evidence file.** Every criterion here that is about
the account cites tests in ``tests/test_phase4_run_evidence.py``, which read the committed
captures through :mod:`edullm_platform.phase4_capture`.

**The run records do not expire and the configuration records do.** That split is new in
this phase and is deliberate. A ``RecordedEventModel`` says a job ran and wrote a
checkpoint whose digest is in the bucket; nothing about the passage of time makes that less
true. A ``FreshEvidenceModel`` says a compute environment is configured a certain way
today, which is one console click from being false -- so criteria 4, 7 and 8 go red thirty
days after their capture, and re-running the capture is what the window is asking for. The
two deferrals cite nothing and so expire from nothing; what reopens them is their trigger
and a person, which is the weaker arrangement and is said out loud beside each of them.

**Criterion 7 rests on a policy statement rather than on a refusal somebody received, and
says so.** A live cross-team denial needs a principal that can assume the workload role,
and the trust policy names the Batch and ECS task services rather than any human. What is
asserted is what the deployed grant permits, read from the account. That is honest and is
one step weaker than a container being told no.
"""

from __future__ import annotations

from typing import Final

from edullm_platform.criteria import (
    CriterionSpec,
    CriterionStatus,
    validate_criterion_specs,
)

__all__ = [
    "A_NARROW_LIST_IS_THE_CONTROL",
    "A_REFUSAL_RATHER_THAN_A_POLICY",
    "A_SECOND_SHAPE_OR_A_QUEUE_THAT_HURTS",
    "CONFIGURATION_CAPTURES_EXPIRE",
    "PHASE4_CRITERION_COUNT",
    "phase4_criteria",
]

PHASE4_CRITERION_COUNT: Final = 11

#: The tests that read the committed captures of the three GPU jobs. Every criterion that
#: is about the account rather than about a template or a pure function cites this module.
RUN_EVIDENCE = "tests/test_phase4_run_evidence.py"
CHECKPOINTS = "tests/test_phase4_checkpoints.py"
SUBMISSION = "tests/test_phase4_training_submission.py"
EXECUTION = "tests/test_phase3_execution.py"
INFRA = "tests/test_phase3_infrastructure.py"
PROFILES = "tests/test_compute_profiles.py"

#: Why the configuration-derived criteria are not settled forever. Attached only to the
#: criteria that rest on a ``FreshEvidenceModel``; the run records carry no such note,
#: because a run that happened does not stop having happened.
CONFIGURATION_CAPTURES_EXPIRE: Final = (
    "This rests on a capture of how the account is configured, which is a statement about "
    "one moment. The record is a FreshEvidenceModel, so thirty days after it was taken it "
    "stops loading, the cited tests fail and this criterion is a gap again with the gate "
    "red. That is the window working: a compute environment can be edited in a console, "
    "and the only thing establishing it has not been is somebody going and looking again."
)

#: How this criterion stopped resting on a policy document. Kept as prose rather than
#: deleted, because the difference between the two is what a reader of it needs.
A_REFUSAL_RATHER_THAN_A_POLICY: Final = (
    "This used to be asserted from the deployed policy document, which says what a grant "
    "is rather than what happened when something reached for it. It is now a refusal a "
    "container actually received: a run whose program probed prefixes it must not reach "
    "and recorded what S3 said. Every probe came back AccessDenied.\n\n"
    "TWO OF THE FOUR PROBES THAT ONCE RAN HAVE BEEN DELETED, AND THE REASON IS THE SAME "
    "REASON THIS CRITERION'S STATEMENT WAS REWRITTEN. They aimed at a cross-team boundary "
    "the role no longer has: infra/iam/batch-gpu-roles.yaml grants PutObject and GetObject "
    "unconditioned on teams/*/runs/*, which the probe key matched. So the write probe would "
    "have come back allowed and failed the run after the GPU was paid for, and the read "
    "probe would have come back NoSuchKey and passed while establishing nothing. Measured "
    "against the deployed role on 2026-08-04: both were allowed. A check that cannot fail "
    "is worse than an absent one, because a criterion cites it.\n\n"
    "The distinction that makes the remaining two worth anything is AccessDenied against "
    "NoSuchKey. The second means the role was permitted to look and found nothing, which is "
    "exactly what a role granting everything returns from an empty prefix -- so a probe "
    "recording 'the call failed' would establish no isolation at all. Neither of the two "
    "that remain can be answered that way: listing the outputs bucket from its root is "
    "outside the StringLike condition on the ListBucket grant, and the lineage bucket "
    "appears in no Resource on the role at all.\n\n"
    "A container is the only principal that can produce this. The workload role's trust "
    "policy names the Batch and ECS task services, so no human can assume it and be "
    "refused. iam:SimulatePrincipalPolicy is not a substitute: it reported ten EC2 actions "
    "as denied in both regions when seven are authorized in one."
)

#: Why the one-item instance list is the control rather than a shortfall. Written out
#: because the criterion's own wording reads like a defect and the record has to argue
#: otherwise on its own, without a reader having to find this module's docstring first.
A_NARROW_LIST_IS_THE_CONTROL: Final = (
    "THE SINGLE-ITEM INSTANCE LIST IS THE CONTROL RATHER THAN AN OMISSION, AND THAT IS WHAT "
    "MAKES THIS A DECISION INSTEAD OF UNFINISHED CONFIGURATION. The GPU compute environment "
    "lists exactly one instance type, so there is no alternate to place onto, and closing "
    "this is one line in infra/batch-compute-gpu.yaml plus a redeploy. The same line is "
    "what stops a submission for a cheap shape landing on an expensive one: g5.xlarge is "
    "$1.006/hr and g5.12xlarge is $5.672, so widening the list the obvious way means a job "
    "that asked for one A10G can be given four. The narrowness is deliberate and what it "
    "buys is a bill bounded by the shape the submitter chose.\n\n"
    "So the work is not typing the line, it is choosing which shape it names, and that "
    "choice is a cost decision with somebody's name on it rather than a typo to fix "
    "overnight. The catalog already prices two single-GPU shapes cheaper than the promoted "
    "one -- gpu-1xt4 on g4dn.xlarge at $0.526 and gpu-1xl4 on g6.xlarge at $0.805, both "
    "unprovisioned -- so a second shape that widens placement without widening the bill is "
    "available to whoever takes the decision.\n\n"
    "Recorded as a gap this read as something nobody had got round to, and the next person "
    "to read it would close it the fast way, which is the outcome the narrow list exists to "
    "prevent. It is not a claim that waiting is free forever: if a run ever waits long "
    "enough to cost more than the placement risk, the answer changes, and the trigger is "
    "what makes somebody look rather than infer."
)

#: What makes criterion 11 live again. Two events rather than a state, because a trigger
#: nobody will observe is a gap wearing a deferral's label.
A_SECOND_SHAPE_OR_A_QUEUE_THAT_HURTS: Final = (
    "A decision to promote a second single-GPU shape, or GPU contention making the wait "
    "expensive enough to be worth the placement risk -- whichever comes first. The first is "
    "an event because promoting a profile is: the catalog prices the candidates already, "
    "and the decision is which of them the compute environment should also accept. The "
    "second is an event somebody feels rather than measures: today one team of three shares "
    "this account's GPU capacity and a wait surprises nobody, and that stops being true the "
    "first time two runs want the same shape at once. Re-record this as covered against a "
    "capture of the widened environment, or re-argue the deferral and say why waiting is "
    "still the cheaper answer; leaving it deferred without either, once a second shape is "
    "provisioned, is the one outcome this trigger forbids."
)

#: Why a criterion about what the role reaches also cites a test about the reader that
#: measures reach. Attached to 4, 7 and 12, which are the three that cite
#: test_the_role_permits_exactly_the_prefix_shape_the_platform_derives.
THE_INSTRUMENT_IS_CITED_BESIDE_THE_MEASUREMENT: Final = (
    "Two of that measurement's four assertions are negative -- the role cannot reach a "
    "scratch prefix, and cannot reach anything outside teams/ at all -- and a negative "
    "assertion is worth exactly what the reader behind it is worth. capture_role_scope "
    "recorded the key portion of every S3 object ARN without looking at the bucket, so a "
    "grant of s3:GetObject on edullm-data/* would have contributed the key pattern *, which "
    "fnmatch matches against every candidate. Both negatives would have flipped to true "
    "with nothing red anywhere.\n\n"
    "So the test guarding the reader is cited here rather than left to the full suite. The "
    "gate runs the node ids its criteria name and no others, which means a regression test "
    "outside that selection protects nothing this criterion rests on, however green it is "
    "in a full run. The grant that would trigger the defect has not been made yet; citing "
    "the guard now costs one node id, and citing it after the grant lands would be a repair."
)


def _ids(module: str, name: str, *params: str) -> tuple[str, ...]:
    """Node ids for one test, with its parametrizations spelled out.

    A parametrized test collects only under its full node id, so citing the bare name names
    nothing at all -- which the gate reports as ``cited_test_missing`` rather than passing.
    """
    if not params:
        return (f"{module}::{name}",)
    return tuple(f"{module}::{name}[{param}]" for param in params)


def phase4_criteria() -> tuple[CriterionSpec, ...]:
    """The eleven Phase 4 acceptance criteria, in the master plan's order.

    Numbered 1 to 12 with 9 absent. See the module docstring for why the hole is left open
    rather than closed up.
    """
    specs = (
        CriterionSpec(
            number="1",
            statement="The container detects the expected GPU.",
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
            proving_node_ids=(
                *_ids(RUN_EVIDENCE, "test_the_process_itself_found_a_cuda_device_rather_than_being_offered_one"),
                *_ids(RUN_EVIDENCE, "test_the_driver_saw_the_shape_the_promoted_profile_asked_for"),
            ),
            supporting_node_ids=(
                *_ids(RUN_EVIDENCE, "test_the_job_that_trained_asked_for_a_gpu_and_the_scheduler_gave_it_one"),
                *_ids(RUN_EVIDENCE, "test_the_gpu_environment_uses_the_nvidia_ami_rather_than_the_default_one"),
                *_ids(SUBMISSION, "test_the_program_refuses_to_train_on_a_processor_it_was_not_asked_for"),
            ),
            scope_limits=(
                (
                    "The sharpest check in the phase, and the reason nine of the eleven are "
                    "pilot-blocking. A container that fails to detect its GPU and trains on the "
                    "CPU produces a run that looks successful, costs GPU rates, and yields a "
                    "result nobody can trust. There is no symptom: the logs, the lineage records "
                    "and the exit code are all indistinguishable from a correct run."
                ),
                (
                    "Three independent answers, because each one alone is satisfiable by a "
                    "different failure. Batch says a GPU was requested; nvidia-smi says a device "
                    "is on the other side of the injected nodes; torch says it allocated on it. A "
                    "CPU build passes the first two, and a missing resource requirement passes "
                    "none -- but a wrong AMI passes the first."
                ),
                (
                    "The device is asserted to be an A10G rather than any GPU. The catalog "
                    "prices gpu-1xa10g on the basis that it is one A10G with 24GB, so a job that "
                    "landed on a T4 would run, would train, and would make the cost estimate in "
                    "the decision record wrong in a store nothing rewrites."
                ),
            ),
        ),
        CriterionSpec(
            number="2",
            statement="A short training step completes.",
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
            proving_node_ids=(
                *_ids(RUN_EVIDENCE, "test_a_real_model_went_through_a_real_optimizer_and_the_loss_moved"),
            ),
            supporting_node_ids=(
                *_ids(RUN_EVIDENCE, "test_the_process_itself_found_a_cuda_device_rather_than_being_offered_one"),
                *_ids(RUN_EVIDENCE, "test_the_run_built_its_dataset_from_the_release_its_submission_named"),
                *_ids(RUN_EVIDENCE, "test_every_shard_the_run_opened_came_from_the_release_it_named"),
                *_ids(RUN_EVIDENCE, "test_the_release_the_run_opened_is_the_one_the_registry_publishes"),
                *_ids(RUN_EVIDENCE, "test_the_run_drew_corpus_bytes_in_a_quantity_no_metadata_read_could_produce"),
                *_ids(SUBMISSION, "test_the_step_count_reaches_the_checkpoint_prefix_and_the_loop_together", "1", "20", "500"),
            ),
            scope_limits=(
                (
                    "A hundred and fifty steps of olmo2_190M, and the loss is asserted to have "
                    "fallen rather than to have reached anything. The direction establishes that "
                    "the optimizer applied gradients -- a loss identical at the first step and "
                    "the last is a backward pass that did nothing -- and the magnitude over a "
                    "hundred and fifty steps is not a claim about learning."
                ),
                (
                    "A published corpus, and which one is measured rather than declared. This "
                    "used to record synthetic tokens as a deliberate limit, on the ground that "
                    "the corpus a run reads is a research question. It is a platform question "
                    "as well: the upstream example hard-codes a path, so a run that ignored the "
                    "release on its submission would report a loss curve, write a checkpoint and "
                    "leave a lineage record naming a corpus nothing opened. What is now asserted "
                    "is the resolved shard list the training program saved beside its weights, "
                    "against the release the container was told to read and the address the "
                    "registry publishes it at."
                ),
                (
                    "A read of 150MiB is not a training run. What the corpus evidence closes is "
                    "the path from a form field to memmapped bytes; whether a model trained this "
                    "way is any good is a research question and no part of this."
                ),
            ),
        ),
        CriterionSpec(
            number="3",
            statement=(
                "W&B receives the expected run ID, config, metrics, and system telemetry."
            ),
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
            proving_node_ids=(
                *_ids(RUN_EVIDENCE, "test_the_wandb_run_is_named_for_the_run_id_and_lives_in_the_platforms_project"),
            ),
            supporting_node_ids=(
                *_ids(EXECUTION, "test_the_wandb_project_comes_from_the_manifest_and_not_from_the_command"),
                *_ids(SUBMISSION, "test_the_form_lets_the_platform_choose_the_identity_it_will_be_charged_under"),
            ),
            scope_limits=(
                (
                    "Read from the container's own log rather than from the W&B API. The run "
                    "URL, the project and the metric keys are what the process reported "
                    "publishing; confirming they arrived would need a W&B credential in this "
                    "repository, which is the one thing D4 keeps out of it."
                ),
                (
                    "System telemetry is W&B's own, collected by its client rather than emitted "
                    "by this platform. What is asserted is that the run exists under the "
                    "platform's project with the platform's run id; the GPU and process series "
                    "beside it are the client's doing and are not separately captured."
                ),
                (
                    "The project comes from the manifest and not from the command, which is the "
                    "half that matters for attribution. A shared W&B account authenticates and "
                    "does not attribute, so a program that named its own project would let a "
                    "submitter file spend under somebody else's budget with lineage and W&B "
                    "disagreeing and nothing able to detect it."
                ),
            ),
        ),
        CriterionSpec(
            number="4",
            statement="S3 receives outputs only under the authorized run prefix.",
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
            proving_node_ids=(
                *_ids(RUN_EVIDENCE, "test_nothing_in_the_outputs_bucket_sits_outside_an_authorized_prefix"),
            ),
            supporting_node_ids=(
                *_ids(RUN_EVIDENCE, "test_the_role_permits_exactly_the_prefix_shape_the_platform_derives"),
                *_ids(RUN_EVIDENCE, "test_a_grant_on_another_bucket_does_not_widen_what_the_outputs_reach_reports"),
                *_ids(RUN_EVIDENCE, "test_the_prefix_the_container_was_given_is_the_one_the_platform_derives"),
            ),
            scope_limits=(
                (
                    "The capture lists the whole bucket rather than the run's own prefix, and it "
                    "has to. This is a claim about what is *absent* elsewhere, and a record "
                    "scoped to the authorized prefix could only ever report that what is there "
                    "is there."
                ),
                (
                    "One team exists, so 'only under the authorized prefix' is currently a claim "
                    "about one prefix. It becomes a stronger claim, not a different one, when a "
                    "second team is bound."
                ),
                THE_INSTRUMENT_IS_CITED_BESIDE_THE_MEASUREMENT,
                CONFIGURATION_CAPTURES_EXPIRE,
            ),
        ),
        CriterionSpec(
            number="5",
            statement=(
                "A checkpoint is resumable only after its manifest and success marker exist."
            ),
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
            proving_node_ids=(
                *_ids(CHECKPOINTS, "test_a_payload_with_no_marker_beside_it_is_not_resumable"),
                *_ids(CHECKPOINTS, "test_the_manifest_is_populated_for_the_committed_state_and_for_no_other"),
                *_ids(RUN_EVIDENCE, "test_the_checkpoint_the_run_wrote_is_one_this_platform_will_resume_from"),
            ),
            supporting_node_ids=(
                *_ids(CHECKPOINTS, "test_the_payload_is_written_before_the_marker_that_certifies_it"),
                *_ids(CHECKPOINTS, "test_a_manifest_with_no_marker_refuses_to_produce_a_resume_reference"),
                *_ids(RUN_EVIDENCE, "test_what_the_run_said_it_wrote_is_what_the_store_says_it_holds"),
                *_ids(RUN_EVIDENCE, "test_a_checkpoint_one_run_wrote_was_loaded_back_by_another"),
                *_ids(RUN_EVIDENCE, "test_the_run_that_was_resumed_from_is_one_whose_checkpoint_is_committed"),
                *_ids(RUN_EVIDENCE, "test_a_resume_restores_a_model_and_not_a_training_run"),
            ),
            scope_limits=(
                (
                    "Proved twice over, and the two are doing different work. The unit tests "
                    "establish that the reader never returns a resumable manifest for a prefix "
                    "with no marker -- the property -- against a store that attests its own "
                    "digests. The run evidence establishes that a real 762MB checkpoint in the "
                    "real bucket is one that reader accepts."
                ),
                (
                    "The ordering is the mechanism and is asserted separately. Payload then "
                    "marker means an interruption leaves a checkpoint that reads unusable; "
                    "reversed, the same interruption leaves a marker certifying a payload that "
                    "was never written."
                ),
                (
                    "A second run has now resumed from it, so this is no longer only a claim "
                    "about the reader. A later run downloaded the 762MB payload, loaded 135 "
                    "tensors with strict=True, and trained on. The evidence is the loss "
                    "rather than the digest: a freshly initialised olmo2_190M on random "
                    "tokens starts near 11.0 and that run started at 9.71, which nothing but "
                    "trained weights in the model produces."
                ),
                (
                    "A resume restores a MODEL AND NOT A TRAINING RUN, and the difference is "
                    "worth stating because 'resumable checkpoint' reads as more than it is. "
                    "The checkpoint carries the state dict and the step and no optimizer "
                    "state, so a resumed AdamW begins with no moment estimates -- which is "
                    "why that run's last loss is above its first. Closing the gap means "
                    "checkpointing the optimizer, which is a change to the training program "
                    "rather than to this platform."
                ),
            ),
        ),
        CriterionSpec(
            number="6",
            statement="An incomplete checkpoint is ignored.",
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
            proving_node_ids=(
                *_ids(CHECKPOINTS, "test_a_marker_with_no_payload_beside_it_is_not_resumable"),
                *_ids(CHECKPOINTS, "test_a_marker_certifying_bytes_the_store_does_not_hold_is_refused"),
                *_ids(CHECKPOINTS, "test_the_resume_helper_answers_nothing_for_every_state_that_is_not_committed"),
            ),
            supporting_node_ids=(
                *_ids(CHECKPOINTS, "test_a_payload_the_store_will_not_attest_is_refused_rather_than_assumed_good"),
                *_ids(CHECKPOINTS, "test_a_marker_whose_byte_count_disagrees_with_the_store_is_refused"),
                *_ids(CHECKPOINTS, "test_an_empty_prefix_is_absent_rather_than_incomplete"),
                *_ids(CHECKPOINTS, "test_a_store_failure_that_is_not_a_missing_object_is_raised_rather_than_read_as_absence", "model.pt", "_SUCCESS"),
            ),
            scope_limits=(
                (
                    "Four ways a checkpoint can be incomplete, and they are not one case "
                    "repeated. Nothing there; a payload nobody certified; a marker whose payload "
                    "is gone; a marker and a payload that describe different bytes. The last is "
                    "the one a naive reader passes, and it is what a retry after a half-finished "
                    "commit actually produces."
                ),
                (
                    "The reader verifies rather than trusts, and that is what makes the "
                    "unconditional write safe. A write-once rule would refuse a retry's payload "
                    "and then let the retry's marker certify the dead attempt's -- fail-closed, "
                    "by losing a good checkpoint and keeping a bad one."
                ),
                (
                    "A store failure that is not an absence is raised rather than read as 'no "
                    "checkpoint'. Without that, a throttle sends a resuming job back to step "
                    "zero on a bad afternoon, spending the training budget a second time and "
                    "looking from outside exactly like a first run."
                ),
            ),
        ),
        CriterionSpec(
            number="7",
            statement=(
                "The workload role reaches run outputs and nothing else: it cannot enumerate "
                "the outputs bucket, and it cannot write to the store that records what it did."
            ),
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
            proving_node_ids=(
                *_ids(RUN_EVIDENCE, "test_the_workload_role_was_refused_the_two_reaches_widening_did_not_grant"),
                *_ids(RUN_EVIDENCE, "test_the_container_could_not_write_to_the_store_that_records_what_it_did"),
            ),
            supporting_node_ids=(
                *_ids(RUN_EVIDENCE, "test_the_role_permits_exactly_the_prefix_shape_the_platform_derives"),
                *_ids(RUN_EVIDENCE, "test_a_grant_on_another_bucket_does_not_widen_what_the_outputs_reach_reports"),
                *_ids(RUN_EVIDENCE, "test_the_prefix_the_container_was_given_is_the_one_the_platform_derives"),
                *_ids(SUBMISSION, "test_both_probes_are_asserted_rather_than_merely_recorded"),
                *_ids(SUBMISSION, "test_the_gate_over_the_probes_fails_the_run_when_a_boundary_is_wrong"),
                *_ids(
                    SUBMISSION,
                    "test_every_probe_that_remains_names_something_the_deployed_template_refuses",
                ),
            ),
            scope_limits=(
                A_REFUSAL_RATHER_THAN_A_POLICY,
                (
                    "THE STATEMENT WAS REWRITTEN WHEN THE GPU ROLE WAS WIDENED, AND THE OLD ONE "
                    "IS HERE SO THE CHANGE IS LEGIBLE. It read: 'The workload role cannot read "
                    "another team's restricted prefix.' The GPU trio was scoped to "
                    "teams/platform/runs/* to make exactly that closeable -- a role permitting "
                    "every team cannot fail to reach another team's prefix -- which is a grant "
                    "shaped by a check rather than by a requirement. Isolation between the "
                    "groups sharing this account is not a goal: they are one team building one "
                    "model, and nobody can name a harm that follows from one reading another's "
                    "outputs. The role now reads teams/*/runs/*, matching the CPU role, so the "
                    "criterion states the containment that survives rather than a property the "
                    "platform decided against."
                ),
                (
                    "Two of the four captured probes no longer describe the deployed role. "
                    "read_another_teams_prefix and write_to_another_teams_prefix were refused "
                    "when captured and would now succeed; that capture stays committed because "
                    "it records an event that happened, and it is cited as supporting rather "
                    "than proving for that reason. The two that still hold are the two the "
                    "evidence model itself calls load-bearing: list_the_whole_outputs_bucket, "
                    "which the s3:prefix condition still bounds, and write_to_the_lineage_bucket."
                ),
                (
                    "A separate GPU role trio exists rather than the CPU roles being tightened. "
                    "Tightening them would have drifted every committed Phase 3 role capture, "
                    "and a phase should not invalidate the previous phase's evidence to close "
                    "its own check."
                ),
                THE_INSTRUMENT_IS_CITED_BESIDE_THE_MEASUREMENT,
                CONFIGURATION_CAPTURES_EXPIRE,
            ),
        ),
        CriterionSpec(
            number="8",
            statement=(
                "Secrets do not appear in GitHub, Batch, CloudWatch, W&B, or S3 records."
            ),
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
            proving_node_ids=(
                *_ids(RUN_EVIDENCE, "test_the_wandb_key_reaches_the_container_without_passing_through_any_record"),
                *_ids(RUN_EVIDENCE, "test_the_key_did_not_turn_up_in_the_log_the_run_wrote"),
            ),
            supporting_node_ids=(
                *_ids(RUN_EVIDENCE, "test_a_capture_that_is_not_there_is_reported_rather_than_read_as_nothing_to_prove"),
            ),
            scope_limits=(
                (
                    "The mechanism is what carries this rather than the scan. The job definition "
                    "names the secret under secrets/valueFrom and Batch resolves it at container "
                    "start, so the value exists in the running container's memory and nowhere "
                    "else -- not in the definition, not in a DescribeJobs answer, not in the log."
                ),
                (
                    "The log scan is shape-based and therefore imperfect in both directions. It "
                    "cannot recognise every secret, and it cannot tell a bare sixty-four "
                    "character digest from a credential. The digests it was told to ignore are "
                    "listed in the record by value, each one verified against what the store "
                    "attests, so a reader can check every exemption rather than trust a pattern."
                ),
                (
                    "GitHub and W&B are not scanned here. The GitHub half is Phase 2's captured "
                    "secret inventory, and W&B's stored config is what this platform sent it, "
                    "which is the container environment already read."
                ),
                CONFIGURATION_CAPTURES_EXPIRE,
            ),
        ),
        # There is no criterion 9. Capacity failure is owned beside the queue-wait detector
        # it cannot be closed without, which is unbuilt, and the number is a
        # deliberate hole so that every citation written against this list goes on naming
        # what it named. The module docstring says why, and what the check protected is
        # printed on the summary every accepted submission ends on, held there by
        # tests/test_pilot_limitations.py.
        CriterionSpec(
            number="10",
            statement=(
                "A job held in RUNNABLE past the queue-wait threshold is detected, and the "
                "queue timeout ends it rather than leaving it queued indefinitely."
            ),
            status=CriterionStatus.DEFERRED,
            deferral_trigger=(
                "The first run that sits in RUNNABLE long enough for somebody to notice, or a "
                "second team being bound -- whichever comes first. Both change the same thing: "
                "today a pilot of three watches their own job and a wait is visible to the "
                "person who caused it, and neither stays true once somebody is waiting on "
                "capacity a run they did not submit is holding."
            ),
            deferral_reason=(
                "Deferred past the first GPU run, deliberately, and this is a decision rather "
                "than unfinished work. A queued job bills nothing, so nothing is at risk while "
                "it waits, and a pilot of three watches their own job.\n\n"
                "It also cannot be closed the way the master plan first assumed. AWS Batch "
                "publishes no CloudWatch metric for queue depth or job state, so there is no "
                "series to threshold and no alarm to configure. The detector has to be built -- "
                "a scheduled ListJobs poll, or an absence-of-expected-lifecycle-event check over "
                "a window -- which is a rule and a Lambda, sized and tested like anything else. "
                "Marking it pilot-blocking would have put that in front of the first GPU run for "
                "no proportionate gain."
            ),
            scope_limits=(
                (
                    "The queue-timeout half is a Batch job attribute rather than an observation "
                    "and could be set today. It is deferred with the detector because a timeout "
                    "that ends a job nobody was watching converts silence into a different "
                    "silence."
                ),
            ),
        ),
        CriterionSpec(
            number="11",
            statement=(
                "A job whose preferred instance type is unavailable is placed on an alternate "
                "permitted type rather than waiting for the one shape."
            ),
            status=CriterionStatus.DEFERRED,
            deferral_trigger=A_SECOND_SHAPE_OR_A_QUEUE_THAT_HURTS,
            deferral_reason=A_NARROW_LIST_IS_THE_CONTROL,
            scope_limits=(
                (
                    "Availability rather than harm, which is why it is not pilot-blocking: a job "
                    "that waits costs nothing and a pilot user of three notices. It matters more "
                    "than it looks because us-east-2 denies RunInstances, so instance-type "
                    "breadth inside us-east-1 is the only lever on availability the account has."
                ),
                (
                    "All five subnets are usable for this shape, so placement breadth across "
                    "zones is not the constraint. That is asserted from "
                    "describe-instance-type-offerings and never from a dry-run, which answers "
                    "authorization and returned DryRunOperation for two shapes in a zone that "
                    "offers neither."
                ),
                (
                    "THIS DEFERRAL CITES NO TEST, SO NOTHING HERE EXPIRES AND NOTHING REOPENS "
                    "IT ON ITS OWN. The criteria resting on captures go red thirty days after "
                    "the capture was taken and say so; this one has no capture under it, so the "
                    "only thing that makes it live again is somebody reading the trigger and "
                    "acting. That is weaker than a window and it is the honest description of "
                    "what a deferral is, which is why it is recorded here rather than left for "
                    "a reader to work out from the absence of citations."
                ),
            ),
        ),
        CriterionSpec(
            number="12",
            statement=(
                "The prefix in the result manifest, the prefix in the workload role, and the "
                "prefix the container is given are the same prefix."
            ),
            status=CriterionStatus.COVERED,
            pilot_blocking=True,
            proving_node_ids=(
                *_ids(RUN_EVIDENCE, "test_the_prefix_the_container_was_given_is_the_one_the_platform_derives"),
                *_ids(RUN_EVIDENCE, "test_the_role_permits_exactly_the_prefix_shape_the_platform_derives"),
            ),
            supporting_node_ids=(
                *_ids(RUN_EVIDENCE, "test_a_grant_on_another_bucket_does_not_widen_what_the_outputs_reach_reports"),
                *_ids("tests/test_phase3_lifecycle_projection.py", "test_the_prefix_recorded_is_the_prefix_the_container_was_handed"),
                *_ids("tests/test_phase3_lifecycle_projection.py", "test_a_succeeded_job_whose_output_cannot_be_located_is_refused", "the variable is absent-environment0", "the prefix names somebody else's bucket-environment1"),
            ),
            scope_limits=(
                (
                    "Added by this phase rather than taken from the master plan, because Phase 4 "
                    "inherited three answers to where a run writes and two of them agreed. The "
                    "result manifest said {bucket}/{run_id}/, the role permitted "
                    "{bucket}/teams/data-prep/runs/*, and the container was told the third. "
                    "Nothing failed, because the CPU smoke command wrote no output at all."
                ),
                (
                    "There is now one function, contracts/results.py::output_prefix, and the "
                    "manifest no longer derives anything -- the recorder reads the prefix out of "
                    "the container's own environment in the Batch event. Two sources that read "
                    "the same value cannot disagree; three literals that happen to match can, "
                    "and did."
                ),
                (
                    "The team segment is real now. All four Phase 3 runs declared team platform "
                    "while every prefix said data-prep, which was a placeholder that happened to "
                    "be self-consistent."
                ),
                (
                    "The role's side of the agreement is a reading rather than an observation, "
                    "so what does the reading is part of the claim. The other two sides are "
                    "values compared directly -- what output_prefix derives against what the "
                    "Batch event says the container was told -- and neither goes through an "
                    "interpreter that can be wrong about its own scope."
                ),
                THE_INSTRUMENT_IS_CITED_BESIDE_THE_MEASUREMENT,
            ),
        ),
    )
    validate_criterion_specs(specs)
    return specs
