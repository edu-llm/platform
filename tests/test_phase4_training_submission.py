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
from typing import Any

import pytest

from edullm_platform.checkpoints import (
    MARKER_OBJECT,
    CheckpointState,
    inspect_checkpoint,
    success_marker_bytes,
)
from tests.fake_object_store import FakeObjectStore
from tools.build_gpu_training_submission import (
    TRAINING_IMAGE_DIGEST,
    dispatch_form,
    marker_writer_source,
    training_program,
    workflow_inputs,
)

COMMIT = "b067a31e48c4038416d179fc85e5f12b05c8d2a9"
BUCKET = "sbsandbox-intern-edullm-outputs"
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
    assert command[-1] == training_program()
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
    from pathlib import Path

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


def test_the_command_is_the_only_field_the_workflow_receives_as_a_list() -> None:
    """Mutation: send the command as a string.

    ``gh workflow run --json`` delivers what it is given, and a string command is a single
    argument the container never splits -- so the whole program becomes the name of a file
    Python is asked to find.
    """
    inputs = workflow_inputs(dispatch_form(commit_sha=COMMIT))

    listed = [name for name, value in inputs.items() if isinstance(value, list)]
    assert listed == ["command"]
    assert all(isinstance(value, str | int | list) for value in inputs.values())


def test_the_form_carries_no_field_the_submission_workflow_would_not_read() -> None:
    """Mutation: add a field here and nowhere else.

    A field the workflow does not declare is dropped silently by ``gh workflow run``, so a
    submission built here would dispatch and mean something different from what it says.
    """
    from pathlib import Path

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

    assert round_tripped["command"][-1] == training_program()
    assert shlex.split(shlex.join(round_tripped["command"])) == round_tripped["command"]
