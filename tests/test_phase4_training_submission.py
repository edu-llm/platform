"""The GPU training submission, and the seam that keeps it honest.

The program the container runs is a string in a form field. Nothing about that is going to
change -- ``python -c`` on whatever survives ``shlex`` is the contract -- so the question is
what can be checked about it from here without a GPU.

Two things can, and they are the two that went wrong in Phase 4. The program has to survive
the round trip the workflow puts it through, because a quote in the wrong place produces a
command that splits into something plausible and different. And the checkpoint it writes has
to be one this platform's reader will accept, because the first training run's program lived
in ``/tmp`` and the protocol it implemented was nobody's to review.

The second is the seam. The program does not import ``edullm_platform`` -- the image does not
carry it -- so the marker writer is embedded by source rather than reimplemented, and these
tests are what confirm the embedding still resolves to the same function.
"""

from __future__ import annotations

import hashlib
import json
import shlex
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from edullm_platform.checkpoints import (
    MARKER_OBJECT,
    CheckpointState,
    inspect_checkpoint,
    success_marker_bytes,
)
from edullm_platform.config import load_yaml
from edullm_platform.contracts.manifest import RunManifest
from edullm_platform.execution import (
    MAXIMUM_CONTAINER_OVERRIDES_BYTES,
    batch_submit_request,
)
from tests.fake_object_store import FakeObjectStore
from tools.build_gpu_training_submission import (
    FOREIGN_TEAM_PREFIX,
    LINEAGE_BUCKET,
    TRAINING_IMAGE_DIGEST,
    dispatch_form,
    dispatch_inputs,
    for_the_wire,
    marker_writer_source,
    training_program,
    workflow_inputs,
)
from tools.build_gpu_training_submission import _measuring_target as measuring_target

COMMIT = "b067a31e48c4038416d179fc85e5f12b05c8d2a9"
BUCKET = "sbsandbox-intern-edullm-outputs"
OUTPUTS_BUCKET = BUCKET
RUN_ID = "run_019fab9d-d1d0-7009-935f-b0189a9c8a86"
PREFIX = f"s3://{BUCKET}/teams/platform/runs/{RUN_ID}/checkpoints/step-20/"
PAYLOAD = b"the weights, or something the size of them"
WRITTEN_AT = datetime(2026, 7, 29, 2, 14, 44, tzinfo=UTC)


def embedded_writer() -> Any:
    """The marker writer as the container would have it: executed out of the program text.

    Reached through the program rather than through the tool's helper, so that a template
    that dropped the interpolation would be caught. Executed in a namespace holding only
    what the program itself imports, which is what proves the extraction is self-contained.
    """
    program = training_program()
    namespace: dict[str, Any] = {"json": json, "datetime": datetime}
    start = program.index("MARKER_SCHEMA_VERSION =")
    end = program.index("# Both halves of the silent failure")
    exec(compile(program[start:end], "<embedded>", "exec"), namespace)  # noqa: S102
    return namespace["success_marker_bytes"]


# ---------------------------------------------------------------------------------------
# The round trip
# ---------------------------------------------------------------------------------------


def test_the_program_survives_the_split_the_workflow_puts_it_through() -> None:
    """Mutation: build the command by concatenation instead of shlex.quote.

    The workflow receives the command as a JSON array and the container runs its elements.
    A program containing a quote that shlex re-splits differently produces a command that
    still parses and runs something else -- on a paid GPU instance, discovered by reading
    the output.
    """
    inputs = workflow_inputs(dispatch_form(commit_sha=COMMIT))
    command = inputs["command"]

    assert isinstance(command, list)
    assert command[:2] == ["python", "-c"]
    assert command[-1] == for_the_wire(training_program())
    assert len(command) == 3, "the program must be one word, not several"


def test_the_program_is_valid_python() -> None:
    """Mutation: any template edit that leaves an unbalanced brace.

    The program is an f-string with literal braces doubled, which is the single easiest
    thing to get wrong in this file and the one whose failure arrives furthest away.
    """
    compile(training_program(), "<training>", "exec")


def test_the_form_names_the_image_the_gpu_job_definition_is_pinned_to() -> None:
    """Mutation: let the digest drift from infra/batch-compute-gpu.yaml.

    Read from the template rather than from a second literal. A submission naming a digest
    the definition does not carry is refused at admission, which is the right answer and a
    slow way to learn it: the refusal names the image, not the disagreement.
    """
    template = (Path(__file__).resolve().parents[1] / "infra" / "batch-compute-gpu.yaml").read_text()

    assert TRAINING_IMAGE_DIGEST in template
    assert dispatch_form(commit_sha=COMMIT)["image_digest"] == TRAINING_IMAGE_DIGEST


def test_the_form_lets_the_platform_choose_the_identity_it_will_be_charged_under() -> None:
    """Mutation: put EDULLM_RUN_ID, the team or the output prefix in the program.

    D4's whole argument. A shared W&B account authenticates but does not attribute, so what
    a run is labelled with has to come from the approved manifest by way of the container's
    environment. A program that named its own would be a submitter choosing whose budget
    the spend lands on, and lineage and W&B would disagree with nothing to detect it.
    """
    program = training_program()

    for variable in (
        "EDULLM_RUN_ID",
        "EDULLM_TEAM",
        "EDULLM_OUTPUT_PREFIX",
        "EDULLM_WANDB_PROJECT",
    ):
        assert f'os.environ["{variable}"]' in program, f"{variable} must be read, not chosen"
    assert "wandb_project" in dispatch_form(commit_sha=COMMIT)


def test_the_program_refuses_to_train_on_a_processor_it_was_not_asked_for() -> None:
    """The sharpest Phase 4 check, asserted where it is cheapest to assert.

    Mutation: drop either assertion. A container that silently trains on the CPU produces a
    run that looks successful, costs GPU rates and yields a result nobody can trust. The
    two conditions are different failures -- a CPU torch build, and a CUDA build with no
    visible device -- and either alone leaves the other open.
    """
    program = training_program()

    assert "assert torch.version.cuda" in program
    assert "assert torch.cuda.is_available()" in program


# ---------------------------------------------------------------------------------------
# The seam: the marker the container writes is the marker this platform reads
# ---------------------------------------------------------------------------------------


def test_the_embedded_marker_writer_is_the_platform_function_and_not_a_copy() -> None:
    """Mutation: paste an equivalent-looking function into the program.

    A copy agrees on the day it is written. What it cannot do is follow a change to the
    marker's shape, and the disagreement would then live in a bucket -- found by whoever
    next tried to resume, which on this platform is a GPU instance that has already
    charged for the training it is about to repeat.
    """
    import inspect as inspection

    assert inspection.getsource(success_marker_bytes) in marker_writer_source()
    assert marker_writer_source() in training_program()


def test_the_embedded_writer_produces_exactly_what_the_platform_writer_produces() -> None:
    """Reads BOTH sides. Mutation: interpolate a stale copy of the source.

    Not "the text is present" -- that is the test above -- but that the text, executed in
    the namespace the container gives it, computes the same bytes. It is what proves the
    function is self-contained: anything it closed over that the program does not supply
    would raise here.
    """
    arguments: dict[str, Any] = {
        "step": 20,
        "payload_name": "model.pt",
        "digest": f"sha256:{hashlib.sha256(PAYLOAD).hexdigest()}",
        "size_bytes": len(PAYLOAD),
        "created_at": WRITTEN_AT,
    }

    assert embedded_writer()(**arguments) == success_marker_bytes(**arguments)


def test_a_checkpoint_written_the_way_the_program_writes_one_reads_back_as_committed() -> None:
    """The end-to-end seam, without a GPU. Mutation: change the marker's key names.

    The program writes the payload, then the marker, and this reproduces exactly that pair
    against the shared store before handing the prefix to the platform's reader. A rename
    on either side turns COMMITTED into CORRUPT here rather than in the bucket.
    """
    store = FakeObjectStore()
    base = f"teams/platform/runs/{RUN_ID}/checkpoints/step-20/"
    digest = f"sha256:{hashlib.sha256(PAYLOAD).hexdigest()}"

    store.put_object(
        Bucket=BUCKET, Key=base + "model.pt", Body=PAYLOAD, ChecksumAlgorithm="SHA256"
    )
    store.put_object(
        Bucket=BUCKET,
        Key=base + MARKER_OBJECT,
        Body=embedded_writer()(
            step=20,
            payload_name="model.pt",
            digest=digest,
            size_bytes=len(PAYLOAD),
            created_at=WRITTEN_AT,
        ),
        ChecksumAlgorithm="SHA256",
    )

    inspected = inspect_checkpoint(store, prefix=PREFIX)

    assert inspected.state is CheckpointState.COMMITTED
    assert inspected.manifest is not None
    assert inspected.manifest.checksum == digest
    assert inspected.manifest.step == 20


def test_the_program_writes_the_payload_before_the_marker() -> None:
    """Mutation: swap the two put_object calls in the template.

    The protocol, checked in the one place a template edit could reverse it. The module the
    marker writer comes from cannot enforce the ordering of two calls the program makes for
    itself, so this is read off the text.
    """
    program = training_program()

    assert program.index('Key=key + "model.pt"') < program.index(f"Key=key + {MARKER_OBJECT!r}")


def test_both_of_the_programs_writes_ask_the_store_to_attest_a_digest() -> None:
    """Mutation: drop ChecksumAlgorithm from either call.

    Without the store's own digest the reader has nothing to compare the marker against and
    reports the checkpoint as unverifiable -- which is correct, and turns a good checkpoint
    into an unusable one at the moment somebody needs it.
    """
    program = training_program()

    assert program.count('ChecksumAlgorithm=\'SHA256\'') + program.count(
        'ChecksumAlgorithm="SHA256"'
    ) == 2


@pytest.mark.parametrize("steps", [1, 20, 500])
def test_the_step_count_reaches_the_checkpoint_prefix_and_the_loop_together(steps: int) -> None:
    """Mutation: hard-code the step in the checkpoint key while the loop stays a parameter.

    The two would agree at twenty and nowhere else, and the disagreement produces a
    checkpoint filed under a step it is not from -- which the marker then certifies,
    truthfully, about the wrong thing.
    """
    program = training_program(steps=steps)

    assert f"checkpoints/step-{steps}/" in program
    assert f"for step in range(1, {steps} + 1):" in program
    assert f'torch.save({{"step": {steps}' in program


# ---------------------------------------------------------------------------------------
# The limit that cost a run, an approval and a submission
# ---------------------------------------------------------------------------------------


def test_the_submitted_program_fits_inside_what_batch_will_accept() -> None:
    """Mutation: submit the program as written.

    NOT HYPOTHETICAL. The version carrying the resume block and the four probes came to
    9,121 bytes, 10,063 once the environment and the JSON were counted. It compiled, it
    validated, it was dispatched, it was approved at the environment gate, it was admitted
    -- and Batch refused it with "Container Overrides length must be at most 8192", naming
    neither the command nor the field that overran.

    Measured through ``batch_submit_request`` rather than over the command's characters,
    because the environment and the JSON punctuation are inside the same budget: a command
    comfortably under the limit can still push the override over it.
    """
    manifest = load_yaml(
        Path(__file__).resolve().parents[1] / "fixtures" / "manifests" / "gpu-routine.yaml",
        RunManifest,
    )
    command = workflow_inputs(dispatch_form(commit_sha=COMMIT))["command"]
    assert isinstance(command, list)

    target = measuring_target()
    request = batch_submit_request(
        manifest=manifest.model_copy(update={"command": tuple(command)}),
        target=target,
        run_id=RUN_ID,
        job_definition_arn=target.job_definition_arn,
    )
    serialized = len(
        json.dumps(request["ContainerOverrides"], separators=(",", ":")).encode("utf-8")
    )

    assert serialized <= MAXIMUM_CONTAINER_OVERRIDES_BYTES, (
        f"the override is {serialized} bytes and Batch accepts "
        f"{MAXIMUM_CONTAINER_OVERRIDES_BYTES}"
    )


def test_the_wire_form_removes_comments_and_changes_nothing_else() -> None:
    """Mutation: strip docstrings too, or strip nothing.

    Comments are 2,581 bytes of this program and exist for somebody reading the tool, which
    is what gets reviewed; nobody reads the command string in a Batch job description.
    Docstrings are kept because they travel inside the platform's own marker writer, and
    removing them would break the property that what runs is that function rather than a
    copy of it.
    """
    written = training_program(resume_from=f"s3://{BUCKET}/teams/platform/runs/x/model.pt")
    wire = for_the_wire(written)

    assert len(wire) < len(written)
    assert "# Both halves of the silent failure" not in wire
    assert "# WHAT THIS ROLE CANNOT REACH" not in wire
    assert '"""' in wire, "docstrings stay; they carry the platform's own function"
    compile(wire, "<wire>", "exec")


def test_a_hash_inside_a_string_is_not_treated_as_a_comment() -> None:
    """Mutation: strip comments with a regex on ``#``.

    The program contains hashes inside string literals, and a pattern-matching stripper
    would truncate the line at the first one -- producing a program that still compiles,
    still runs, and does something different. Tokenising is what makes the distinction.
    """
    source = 'value = "a # b"  # this is the comment\nother = 1\n'

    assert for_the_wire(source) == 'value = "a # b"\nother = 1\n'


def test_the_wire_form_still_carries_the_platforms_marker_writer() -> None:
    """Reads BOTH sides. Mutation: strip docstrings, which would silently break this.

    The embedding is only worth anything if the bytes survive the transformation applied on
    the way to the wire. A stripper that removed docstrings would leave a function that
    behaves the same and is no longer the same source, and the test asserting it *is* the
    same source is checking the unstripped form.
    """
    wire = for_the_wire(training_program())

    assert marker_writer_source() in wire


# ---------------------------------------------------------------------------------------
# The two things only a container can establish
# ---------------------------------------------------------------------------------------


def test_the_isolation_probe_reads_the_code_rather_than_whether_the_call_threw() -> None:
    """Mutation: record the probe as a boolean, or catch every exception as a refusal.

    ``AccessDenied`` means the role may not look. ``NoSuchKey`` means it may look and found
    nothing, which establishes no isolation whatsoever -- and is exactly what a probe
    against an empty prefix returns from a role that permits everything. A boolean cannot
    tell those apart, so the code is what gets recorded.
    """
    program = training_program()

    assert 'return error.response["Error"]["Code"]' in program
    assert 'return "allowed"' in program
    assert "botocore.exceptions.ClientError" in program


def test_the_probe_reaches_for_a_team_nobody_has_bound() -> None:
    """Mutation: probe a team that exists, or one whose prefix might hold an object.

    The probe has to be against a prefix where the two outcomes are distinguishable. A real
    team's prefix could legitimately be empty, so a 404 there would be ambiguous; a team
    nobody has bound certainly holds nothing, which makes anything other than AccessDenied
    a statement that the grant is wider than it reads.
    """
    program = training_program()

    assert FOREIGN_TEAM_PREFIX in program
    assert "not-a-bound-team" in FOREIGN_TEAM_PREFIX


def test_all_four_probes_are_asserted_rather_than_merely_recorded() -> None:
    """Mutation: print the probe results and let the run succeed anyway.

    Recording a refusal that did not happen is worse than not probing at all: the capture
    would say the boundary holds, and a criterion would cite it. The run has to fail if any
    probe came back allowed, which is the difference between evidence and a log line.
    """
    program = training_program()

    for probe in (
        "read_another_teams_prefix",
        "write_to_another_teams_prefix",
        "list_the_whole_outputs_bucket",
        "write_to_the_lineage_bucket",
    ):
        assert probe in program, probe
    assert 'assert not reachable, "this role reached something it must not: "' in program


def test_the_lineage_bucket_probe_names_the_bucket_the_platform_alone_writes_to() -> None:
    """Reads BOTH sides. Mutation: probe the outputs bucket twice.

    The sharpest of the four. Every other grant on this role is arguable; the one that is
    not is that a workload cannot write to the store recording what it did. A probe that
    named the wrong bucket would pass, prove nothing, and read as though it had.
    """
    program = training_program()

    assert OUTPUTS_BUCKET != LINEAGE_BUCKET
    assert f"Bucket={LINEAGE_BUCKET!r}" in program
    assert 'Key="result/" + run_id + ".json"' in program, (
        "the key has to be one the lifecycle recorder itself writes, or the probe tests a "
        "prefix nothing was ever going to grant"
    )


def test_a_resume_loads_the_state_dict_rather_than_only_downloading_it() -> None:
    """Mutation: download the payload and check its digest, without loading it.

    That is the claim the committed evidence already makes -- inspect_checkpoint says the
    marker certifies the payload and the store agrees. What it cannot say is whether torch
    will accept the bytes into this architecture, which is the thing a researcher resuming
    a run actually needs.
    """
    program = training_program(resume_from=f"s3://{BUCKET}/teams/platform/runs/x/model.pt")

    assert "torch.load(" in program
    assert 'model.load_state_dict(restored["model"], strict=True)' in program


def test_a_resume_refuses_a_checkpoint_from_a_different_architecture() -> None:
    """Mutation: pass strict=False.

    A non-strict load accepts a state dict that is missing tensors or carries unexpected
    ones, leaving a model that is silently part somebody else's -- and it trains, and the
    loss looks plausible, and nothing anywhere says which weights came from where.
    """
    program = training_program(resume_from=f"s3://{BUCKET}/teams/platform/runs/x/model.pt")

    assert "strict=True" in program
    assert "strict=False" not in program


def test_a_run_with_nothing_to_resume_from_carries_no_resume_code_at_all() -> None:
    """Mutation: emit the resume block with an empty URI and let it fail at runtime.

    A first run has nothing to resume from, and that is the ordinary case rather than an
    error. Emitting the block with an empty URI would make every first run fail on a
    download of nothing -- on a paid GPU instance, after the image pull.
    """
    program = training_program()

    assert "resumed = {}" in program
    assert "download_fileobj" not in program
    assert "load_state_dict" not in program


def test_the_summary_carries_both_new_sections_so_the_capture_can_read_them() -> None:
    """Mutation: probe and resume, and print neither.

    CloudWatch is the only channel out of the container. A probe whose result never reaches
    the log is a probe nobody can capture, and the criterion would be resting on the fact
    that somebody once watched it happen.
    """
    program = training_program(resume_from=f"s3://{BUCKET}/teams/platform/runs/x/model.pt")

    assert '"isolation": isolation,' in program
    assert '"resumed": resumed,' in program


def test_the_dispatch_payload_is_every_field_as_a_string_including_the_command() -> None:
    """Reads BOTH sides. Mutation: send the command as a list, which is what this did.

    ``workflow_dispatch`` declares every input as ``type: string``, so gh refuses an array
    before the run starts -- with "cannot unmarshal array into Go value of type string",
    which reads like a malformed payload rather than one field of the wrong type. Asserted
    against the workflow's own declarations rather than against a remembered rule.
    """
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "submit-run.yml"
    ).read_text()
    payload = dispatch_inputs(dispatch_form(commit_sha=COMMIT))

    assert all(isinstance(value, str) for value in payload.values())
    for name in payload:
        declared = workflow.split(f"{name}:", 1)[1].split("type:", 1)[1].split("\n", 1)[0]
        # choice as well as string: GitHub resolves a choice to a string on the wire, so
        # both are single strings in the payload. What must not appear is boolean or number,
        # which arrive as their own JSON types and would be refused the same way an array is.
        assert declared.strip() in ("string", "choice"), f"{name} is declared {declared.strip()}"


def test_the_split_the_workflow_performs_is_mirrored_here_and_not_guessed() -> None:
    """Mutation: split the command differently from the runner does.

    The payload carries one shell command line and the workflow splits it with
    ``shlex.split``. Validating locally against a different split would prove a payload
    correct that the runner then builds into something else -- which is the failure the
    local validation exists to prevent.
    """
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "submit-run.yml"
    ).read_text()
    form = dispatch_form(commit_sha=COMMIT)

    assert 'shlex.split(os.environ.get("FORM_COMMAND", ""))' in workflow
    assert workflow_inputs(form)["command"] == shlex.split(
        dispatch_inputs(form)["command"]
    )


def test_the_form_carries_no_field_the_submission_workflow_would_not_read() -> None:
    """Mutation: add a field here and nowhere else.

    A field the workflow does not declare is dropped silently by ``gh workflow run``, so a
    submission built here would dispatch and mean something different from what it says.
    """
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "submit-run.yml"
    ).read_text()
    inputs = workflow_inputs(dispatch_form(commit_sha=COMMIT))

    for name in inputs:
        assert f"{name}:" in workflow, f"{name} is not an input the submission workflow declares"


def test_the_program_round_trips_when_the_form_is_serialized_and_read_back() -> None:
    """Mutation: any change that makes the program unserializable as JSON.

    The payload is written to a file and handed to ``gh workflow run --json``, so the bytes
    that reach GitHub are JSON's, not Python's.
    """
    inputs = workflow_inputs(dispatch_form(commit_sha=COMMIT))
    round_tripped = json.loads(json.dumps(inputs))

    assert round_tripped["command"][-1] == for_the_wire(training_program())
    assert shlex.split(shlex.join(round_tripped["command"])) == round_tripped["command"]
