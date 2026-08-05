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

import ast
import hashlib
import json
import shlex
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from edullm_platform.checkpoints import (
    CHECKSUM_ALGORITHM,
    MARKER_OBJECT,
    CheckpointState,
    inspect_checkpoint,
    success_marker_bytes,
)
from edullm_platform.config import load_yaml
from edullm_platform.contracts.dataset_registry import (
    DatasetRegistry,
    PublishedDatasetReference,
)
from edullm_platform.contracts.manifest import RunManifest
from edullm_platform.execution import (
    MAXIMUM_CONTAINER_OVERRIDES_BYTES,
    batch_submit_request,
)
from tests.fake_object_store import FakeObjectStore
from tools.build_gpu_training_submission import (
    JOB_DEFINITION_PLACEHOLDER_DIGEST,
    LINEAGE_BUCKET,
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


def test_the_form_does_not_name_the_digest_the_job_definition_carries() -> None:
    """Reads BOTH sides. Mutation: copy the job definition's digest into the form, which
    is what this test used to require.

    NOT HYPOTHETICAL, AND THE OLD VERSION OF THIS TEST ENFORCED THE DEFECT. The GPU job
    definitions are pinned to a placeholder, because RegisterJobDefinition substitutes the
    manifest's own digest per run -- so the base definition's image is never the image a
    run pulls. compile_submission separately requires the declared digest to be one the
    declared commit published. A form copying the placeholder therefore compiles for
    exactly one commit and is refused for every other, with a message about an image
    rather than about the copying.

    Measured 2026-08-04: the placeholder is no longer in the registry at all, so the form
    compiled for no commit whatsoever, and a dispatch was refused at the compile job.
    """
    template = (
        Path(__file__).resolve().parents[1] / "infra" / "batch-compute-gpu.yaml"
    ).read_text()

    assert JOB_DEFINITION_PLACEHOLDER_DIGEST in template, (
        "the constant is kept only to explain what the form must not copy; if the template "
        "no longer carries it, re-read the explanation rather than deleting the constant"
    )
    assert dispatch_form(commit_sha=COMMIT)["image_digest"] == ""
    assert (
        dispatch_form(commit_sha=COMMIT, image_digest="sha256:" + "b" * 64)["image_digest"]
        == "sha256:" + "b" * 64
    )


def test_an_unpinned_digest_is_left_out_of_the_dispatch_rather_than_sent_empty() -> None:
    """Mutation: send image_digest as an empty string.

    The workflow's image_digest input is optional and its help says to leave it blank, but
    the resolver reads whether the key is present. dispatch_inputs drops empty values for
    exactly this reason, and that behaviour is load bearing here rather than incidental.
    """
    assert "image_digest" not in dispatch_inputs(dispatch_form(commit_sha=COMMIT))
    pinned = dispatch_inputs(dispatch_form(commit_sha=COMMIT, image_digest="sha256:" + "b" * 64))
    assert pinned["image_digest"] == "sha256:" + "b" * 64


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

    # Read against the module's constant rather than a literal. The write path moved from
    # SHA-256 to CRC32C because S3 cannot give a multipart upload a full-object SHA-256, and
    # a literal here would have held this test green while the program and the reader
    # disagreed about which digest the marker owes.
    assert program.count(f"ChecksumAlgorithm={CHECKSUM_ALGORITHM!r}") == 2


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
        job_definition=target.job_definition_arn,
    )
    serialized = len(
        json.dumps(request["ContainerOverrides"], separators=(",", ":")).encode("utf-8")
    )

    assert serialized <= MAXIMUM_CONTAINER_OVERRIDES_BYTES, (
        f"the override is {serialized} bytes and Batch accepts "
        f"{MAXIMUM_CONTAINER_OVERRIDES_BYTES}"
    )


def published_reference(reference_id: str) -> PublishedDatasetReference:
    """The registry entry for one of the two corpora C2 registered, read from config/.

    Read rather than constructed, because what these tests are about is the platform
    resolving an identifier the submitter chose into facts the submitter did not, and a
    fixture built here would be this test agreeing with itself about the resolution.
    """
    registry = load_yaml(
        Path(__file__).resolve().parents[1] / "config" / "datasets.yaml", DatasetRegistry
    )
    resolved = registry.reference_for(reference_id)

    assert resolved is not None, f"{reference_id} is not a published reference in config/"
    return resolved


def submission_environment(request: dict[str, Any]) -> dict[str, str]:
    return {
        entry["Name"]: entry["Value"] for entry in request["ContainerOverrides"]["Environment"]
    }


def test_the_container_is_told_the_corpus_the_registry_resolved_and_not_the_form_field() -> None:
    """Mutation: forward the reference_id and let the container resolve it.

    That needs the registry inside the image and lets a submitter name a dataset id the
    registry does not carry. The approved manifest names an identifier; the resolution from
    identifier to (dataset_id, version, tokenizer) is the platform's, made once, on this side
    -- the same rule that has the state machine read the image scan itself rather than accept
    findings from a caller, and that has ``wandb_project`` come off the manifest rather than
    out of the command.

    ``EDULLM_DATASET_RELEASE`` still carries the identifier and is not replaced. It is what
    the decision record was written about, so a run whose lineage says one thing and whose
    container read another would be unresolvable after the fact; these three are the
    resolution, beside it rather than instead of it.
    """
    reference = published_reference("regmix-10b-v1")
    manifest = load_yaml(
        Path(__file__).resolve().parents[1] / "fixtures" / "manifests" / "gpu-routine.yaml",
        RunManifest,
    )
    target = measuring_target()

    request = batch_submit_request(
        manifest=manifest.model_copy(update={"dataset_release": reference.reference_id}),
        target=target,
        run_id=RUN_ID,
        job_definition=target.job_definition_arn,
        dataset_reference=reference,
    )
    environment = submission_environment(request)

    assert environment["EDULLM_DATASET_ID"] == "pretrain/regmix-10b"
    assert environment["EDULLM_DATASET_VERSION"] == "v1"
    assert environment["EDULLM_DATASET_RELEASE"] == "regmix-10b-v1"


def test_the_container_is_told_which_tokenizer_and_never_left_to_assume_one() -> None:
    """Mutation: send two variables and let the program hold TokenizerConfig.dolma2().

    THE TWO REGISTERED CORPORA DO NOT SHARE A TOKENIZER. ``pretrain/regmix-10b`` depends on
    ``tokenizer/dolma2-bpe`` and ``pretrain/lean4-mathlib-bytes`` on ``tokenizer/bytes-utf8``,
    both read from ``groups[].depends_on[]`` with role ``tokenizer`` on 2026-08-01.

    The upstream family file refuses this exact default on its own side and records the
    reason: a mismatched tokenizer's ids usually still fall in range, so the embedding lookup
    does not raise and the loss curve is merely bad. A constant here is right for whichever
    corpus somebody tested against and silent for the other, which is why this test reads both
    rather than one.

    The tokenizer cannot come from the reader either. ``ResolvedSplit`` does not expose it, so
    a container that wanted it would have to open ``dataset.json`` and walk ``depends_on`` --
    a second implementation of a resolution the platform has already done.
    """
    manifest = load_yaml(
        Path(__file__).resolve().parents[1] / "fixtures" / "manifests" / "gpu-routine.yaml",
        RunManifest,
    )
    target = measuring_target()

    told = {}
    for reference_id in ("regmix-10b-v1", "lean4-mathlib-bytes-v3"):
        reference = published_reference(reference_id)
        request = batch_submit_request(
            manifest=manifest.model_copy(update={"dataset_release": reference_id}),
            target=target,
            run_id=RUN_ID,
            job_definition=target.job_definition_arn,
            dataset_reference=reference,
        )
        told[reference_id] = submission_environment(request)["EDULLM_DATASET_TOKENIZER"]

    assert told == {
        "regmix-10b-v1": "tokenizer/dolma2-bpe",
        "lean4-mathlib-bytes-v3": "tokenizer/bytes-utf8",
    }
    assert len(set(told.values())) == 2, (
        "the two registered corpora were chosen partly because they disagree here; if they "
        "ever agree, this test stops being able to catch a hard-coded tokenizer"
    )


def test_a_run_that_reads_nothing_is_told_nothing_about_a_corpus() -> None:
    """Mutation: emit the three variables unconditionally, empty when there is no corpus.

    ``none`` is the honest answer and the common one -- a quick check reads nothing -- and an
    empty ``EDULLM_DATASET_TOKENIZER`` is worse than an absent one in the same way an empty
    ``WANDB_USERNAME`` is: it reads as a resolution that failed rather than one that was never
    attempted, and a program testing ``if os.environ.get(...)`` would take the empty branch
    while a program reading the variable directly would resolve a tokenizer named "".

    Absent also keeps the budget honest. These three cost nothing on the runs that do not read
    a corpus, which is every run the platform has admitted so far.
    """
    manifest = load_yaml(
        Path(__file__).resolve().parents[1] / "fixtures" / "manifests" / "gpu-routine.yaml",
        RunManifest,
    )
    target = measuring_target()

    request = batch_submit_request(
        manifest=manifest,
        target=target,
        run_id=RUN_ID,
        job_definition=target.job_definition_arn,
    )
    environment = submission_environment(request)

    assert "EDULLM_DATASET_ID" not in environment
    assert "EDULLM_DATASET_VERSION" not in environment
    assert "EDULLM_DATASET_TOKENIZER" not in environment
    assert environment["EDULLM_DATASET_RELEASE"] == manifest.dataset_release


def test_the_three_dataset_variables_fit_beside_a_resume_block() -> None:
    """THE BUDGET TEST, AND THE NUMBERS ARE MEASURED RATHER THAN ASSUMED.

    Batch accepts 8,192 bytes of serialized ``containerOverrides``. The measurement is taken
    through ``batch_submit_request`` rather than over the variables' characters, because the
    JSON punctuation and the key names are inside the same limit -- which is the distinction
    that cost a run, an approval and a submission before ``ContainerOverridesTooLargeError``
    existed.

    Measured 2026-08-01 on this tree, against the largest of the registered corpora by name
    length, carrying the full training command: the override is reported by the assertion
    below when it fails, and the three variables' own cost is asserted rather than described
    so that a rename which lengthens them lands here.

    The figure this plan inherited -- 599 bytes of post-resume headroom, of which two
    variables cost 112 -- was measured against a different tree and is not reused. What is
    asserted instead is the property that matters and survives both tracks landing: the
    override with all three present is inside the limit, and the three cost less than the
    headroom the run without them has.

    If three ever stop fitting, the answer is the master plan's reserved section -- the
    program moves into the image as a named config -- and not a shorter variable name.
    ``EDULLM_DATASET_TOKENIZER`` is long because it says what it is.
    """
    reference = published_reference("lean4-mathlib-bytes-v3")
    manifest = load_yaml(
        Path(__file__).resolve().parents[1] / "fixtures" / "manifests" / "gpu-routine.yaml",
        RunManifest,
    )
    command = workflow_inputs(dispatch_form(commit_sha=COMMIT))["command"]
    assert isinstance(command, list)
    carrying_the_command = manifest.model_copy(
        update={"command": tuple(command), "dataset_release": reference.reference_id}
    )
    target = measuring_target()

    def override_bytes(**extra: Any) -> int:
        request = batch_submit_request(
            manifest=carrying_the_command,
            target=target,
            run_id=RUN_ID,
            job_definition=target.job_definition_arn,
            **extra,
        )
        return len(
            json.dumps(request["ContainerOverrides"], separators=(",", ":")).encode("utf-8")
        )

    without = override_bytes()
    with_corpus = override_bytes(dataset_reference=reference)

    assert with_corpus <= MAXIMUM_CONTAINER_OVERRIDES_BYTES, (
        f"the override carrying the three dataset variables is {with_corpus} bytes and Batch "
        f"accepts {MAXIMUM_CONTAINER_OVERRIDES_BYTES}; the headroom without them was "
        f"{MAXIMUM_CONTAINER_OVERRIDES_BYTES - without}"
    )
    assert with_corpus - without < MAXIMUM_CONTAINER_OVERRIDES_BYTES - without


def test_the_wire_form_removes_the_prose_and_leaves_the_program() -> None:
    """Mutation: strip nothing, or strip something that runs.

    Comments and docstrings are prose. They exist for somebody reading the tool, which is
    what gets reviewed; nobody reads the command string in a Batch job description, and
    two submissions were refused by Batch for carrying them.

    Docstrings were kept until 2026-08-04 and the reason was a real one -- see for_the_wire
    -- so what replaces it has to be a check rather than a rewording. The check is below
    and in the two tests further down: the program still parses, the unstripped text still
    carries the platform writer's exact source, and the stripped writer still computes the
    platform writer's exact bytes.
    """
    written = training_program(resume_from=f"s3://{BUCKET}/teams/platform/runs/x/model.pt")
    wire = for_the_wire(written)

    assert len(wire) < len(written)
    assert "# Both halves of the silent failure" not in wire
    assert "# WHAT THIS ROLE CANNOT REACH" not in wire
    assert '"""' not in wire, "docstrings go too; two Batch refusals bought this line"
    compile(wire, "<wire>", "exec")

    # Nothing that runs was removed with them. Statement counts rather than a text
    # comparison, because a stripper that deleted a line of code would otherwise have to be
    # noticed by eye in a diff of a generated string.
    assert len(ast.parse(wire).body) == len(ast.parse(written).body)


def test_a_hash_inside_a_string_is_not_treated_as_a_comment() -> None:
    """Mutation: strip comments with a regex on ``#``.

    The program contains hashes inside string literals, and a pattern-matching stripper
    would truncate the line at the first one -- producing a program that still compiles,
    still runs, and does something different. Tokenising is what makes the distinction.
    """
    source = 'value = "a # b"  # this is the comment\nother = 1\n'

    assert for_the_wire(source) == 'value = "a # b"\nother = 1\n'


def test_the_wire_forms_marker_writer_computes_what_the_platforms_writer_computes() -> None:
    """Reads BOTH sides. Mutation: reimplement the writer, or strip something that runs.

    THIS TEST USED TO ASSERT THE WIRE FORM CONTAINED THE PLATFORM WRITER'S SOURCE TEXT, AND
    THAT IS NO LONGER TRUE BECAUSE THE WIRE FORM DROPS DOCSTRINGS. What the text assertion
    was protecting is that the container runs the platform's function rather than a copy
    that can drift from it, and that property is not made by the text being identical -- it
    is made by the source being extracted with inspect.getsource instead of typed out, which
    test_the_embedded_marker_writer_is_the_platform_function_and_not_a_copy still asserts
    against the unstripped program.

    What is left to check is the half a text comparison never checked: that the function
    surviving the transformation still computes the same bytes. So it is executed. A copy
    that had drifted, or a stripper that removed a line of code rather than a line of prose,
    fails here rather than in a bucket.
    """
    wire = for_the_wire(training_program())
    namespace: dict[str, Any] = {"json": json, "datetime": datetime}
    start = wire.index("MARKER_SCHEMA_VERSION =")
    end = wire.index("def refusal_for")
    exec(compile(wire[start:end], "<wire-writer>", "exec"), namespace)  # noqa: S102

    written_at = datetime(2026, 8, 5, 1, 2, 3, tzinfo=UTC)
    arguments: dict[str, Any] = {
        "step": 20,
        "payload_name": "model.pt",
        "digest": "sha256:" + "a" * 64,
        "size_bytes": 1024,
        "created_at": written_at,
        "crc32c_digest": "crc32c:" + "b" * 8,
    }

    assert namespace["success_marker_bytes"](**arguments) == success_marker_bytes(**arguments)


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


def test_the_two_probes_that_no_longer_probe_anything_are_gone() -> None:
    """Mutation: keep the four-probe matrix.

    The deployed GPU workload role grants s3:PutObject and s3:GetObject unconditioned on
    teams/*/runs/*, which the old probe key matched. So the write probe came back `allowed`
    and failed the run after the GPU was paid for, and the read probe came back NoSuchKey
    and passed without establishing anything -- the exact distinction refusal_for's own
    docstring was written to insist on. infra/iam/batch-gpu-roles.yaml records the decision
    that widened the role and says the cross-team check encodes no requirement.
    """
    program = training_program()

    assert "read_another_teams_prefix" not in program
    assert "write_to_another_teams_prefix" not in program
    assert "not-a-bound-team" not in program


def test_both_probes_are_asserted_rather_than_merely_recorded() -> None:
    """Mutation: print the probe results and let the run succeed anyway.

    Recording a refusal that did not happen is worse than not probing at all: the capture
    would say the boundary holds, and a criterion would cite it. The run has to fail if any
    probe came back allowed, which is the difference between evidence and a log line.
    """
    program = training_program()

    for probe in ("list_the_whole_outputs_bucket", "write_to_the_lineage_bucket"):
        assert probe in program, probe
    assert 'assert not reachable, "this role reached something it must not: "' in program


def test_the_gate_over_the_probes_fails_the_run_when_a_boundary_is_wrong() -> None:
    """Reads BOTH sides, by running the gate rather than reading it.

    Mutation: compare against the wrong string, or sort a generator and assert on the
    generator. Every other test here asserts that a line of text is present, which cannot
    tell a working gate from a misspelled one -- and a gate that never fires is the same
    shape of defect as the two probes this change deleted.

    Executed against the real deployed role on 2026-08-04 by policy simulation: both probes
    came back implicitDeny, and both came back allowed against a deliberately widened copy
    of the same policy.
    """
    program = training_program()
    start = program.index("reachable = sorted(")
    gate = compile(program[start:], "<gate>", "exec")

    exec(gate, {"isolation": {"list_the_whole_outputs_bucket": "AccessDenied"}})  # noqa: S102

    with pytest.raises(AssertionError, match="write_to_the_lineage_bucket"):
        exec(  # noqa: S102
            gate,
            {
                "isolation": {
                    "list_the_whole_outputs_bucket": "AccessDenied",
                    "write_to_the_lineage_bucket": "allowed",
                }
            },
        )


def test_every_probe_that_remains_names_something_the_deployed_template_refuses() -> None:
    """Mutation: add a probe against a grant the role actually holds.

    This is the check that would have caught the two just deleted, and it is written
    against the template rather than against a memory of it. A probe is only worth its
    bytes if the role could fail it, and the way that stops being true is silent: somebody
    widens a grant for a good reason and the probe underneath it turns into a green light
    wired to nothing.

    The outputs ListBucket grant carries a StringLike condition on s3:prefix, so a list
    from the root is outside it. The lineage bucket appears nowhere in the role at all.
    """
    template = (
        Path(__file__).resolve().parents[1] / "infra" / "iam" / "batch-gpu-roles.yaml"
    ).read_text(encoding="utf-8")
    program = training_program()

    assert "list_the_whole_outputs_bucket" in program
    assert 'Prefix=""' in program
    assert "s3:prefix" in template, (
        "the list probe only means something while the ListBucket grant is conditioned on a "
        "prefix; an unconditioned grant would make listing from the root allowed"
    )

    assert "write_to_the_lineage_bucket" in program
    assert f":s3:::{LINEAGE_BUCKET}" not in template, (
        "the lineage probe only means something while no Resource in the role names that "
        "bucket; the template's prose says it is deliberately absent, and this is the half "
        "of that sentence a deploy could contradict"
    )


def test_the_form_asks_for_the_cheapest_card_and_no_retired_corpus() -> None:
    """Mutation: leave the form pointing at the A10G and at dolma-2026-07.

    The release is the sharper half. config/datasets.yaml marks dolma-2026-07 retired and
    the submission form's dataset_release is a `choice` input, so a dispatch carrying it is
    rejected by GitHub with a message about an invalid input value -- before anything is
    compiled, and naming a field rather than a corpus.
    """
    registry = load_yaml(
        Path(__file__).resolve().parents[1] / "config" / "datasets.yaml", DatasetRegistry
    )
    form = dispatch_form(commit_sha=COMMIT)

    assert form["compute_profile"] == "gpu-1xt4"
    assert form["dataset_release"] == "none"
    assert form["team"] == "platform"
    retired = {one.release_id for one in registry.releases if one.retired}
    assert form["dataset_release"] not in retired


def test_the_program_scores_bytes_and_reports_bits_per_byte() -> None:
    """Mutation: log a loss and call it bits per byte.

    bpb = nats / ln 2 is only a bits-per-byte figure if one token is one byte. The token
    range is what makes that true, so the two assertions belong together: a program drawing
    ids from the whole vocabulary and dividing its loss by ln 2 reports bits per TOKEN under
    a byte's name, and nothing downstream could tell.
    """
    program = training_program()

    assert "torch.randint(0, 256," in program
    assert "LN2 = 0.6931471805599453" in program
    assert '"train/bpb": loss / LN2' in program
    assert '"bytes_scored": len(losses) * 1024,' in program


def test_the_program_asserts_the_uniform_byte_floor() -> None:
    """Mutation: report bpb and assert nothing about it.

    Uniform bytes carry eight bits each. Twenty steps over twenty thousand never-repeated
    tokens cannot get a model below that floor honestly, so a run that reports below it has
    computed something other than what the field is named. The floor is the one number here
    that is known without measuring anything.
    """
    assert "assert min(losses) / LN2 > 8.0" in training_program()


def test_the_program_shifts_its_labels_because_olmo_core_does_not() -> None:
    """THE FLOOR ABOVE CAUGHT THIS ONE FOR REAL. Mutation: pass ``labels=ids``.

    ``Transformer.forward`` hands ``labels`` straight to the LM head. The shift lives in
    ``olmo_core.data.utils.get_labels`` -- "labels are just input IDs shifted to the left
    (first item is ignored)" -- which the trainer calls on the caller's behalf. This program
    builds its own batch, so nothing calls it, and ``labels=ids`` asks the model to predict
    token t from a context already containing token t. A causal model solves that copy in a
    handful of steps and reports a number that is not a language-modelling loss.

    It shipped that way and ran that way, and the floor is the only reason anybody knows:
    run_019fd2bc on 2026-08-05 took bpb from 15.85 to 2.48 in twenty steps, through a floor
    of 8.0. Nothing had reached the assertion before, because until ``botocore[crt]`` was in
    the image every run died at the checkpoint write forty lines above it -- so a defect in
    what the platform's only training program *computes* was being hidden by a defect in
    whether it could *save*.

    Written over the text rather than over a run, because the alternative costs a T4. The
    pad value is asserted with the rest: ``-100`` is what ``get_labels`` uses and what the
    loss ignores, and padding with a real token id would put one garbage prediction into
    every sequence instead.
    """
    program = training_program()

    assert (
        "labels = torch.nn.functional.pad(ids[..., 1:], (0, 1, 0, 0), value=-100)" in program
    )
    assert "model(ids, labels=labels)" in program
    assert "model(ids, labels=ids)" not in program, (
        "unshifted labels make this a copy task, which the uniform-byte floor rejects"
    )


def test_the_program_refuses_to_train_in_anything_but_float32_on_this_card() -> None:
    """Mutation: leave the dtype to whatever the recipe defaults to.

    gpu-1xt4 is a g4dn.xlarge and the T4 is Turing, which has no hardware bfloat16 --
    torch.cuda.is_bf16_supported() returns true there and emulates it, so a bf16 run does
    not crash. It trains slowly and reports numbers that are not the numbers asked for.
    """
    program = training_program()

    assert "assert all(p.dtype is torch.float32 for p in model.parameters())" in program
    assert "torch.bfloat16" not in program
    assert "bf16" not in program


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
