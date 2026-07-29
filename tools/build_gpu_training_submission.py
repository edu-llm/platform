"""Build the GPU training program and the dispatch form that carries it.

**This file exists because the first GPU training run's program lived in ``/tmp``.** It was
written once, ran once, wrote a checkpoint the platform still holds, and could not be
reviewed, tested, diffed or reproduced. The protocol it implemented -- payload first, then
the marker -- was correct, and nothing would have caught it if it had not been.

The program is a string because that is what it is on the wire: the command is a form field,
and the container runs ``python -c`` on whatever survives ``shlex``. Getting a quote wrong
there produces a command that splits into something plausible and different, which is why
the form is built here rather than hand-escaped into JSON.

**The marker writer is not reimplemented here; it is the platform's own function, extracted
by source.** :func:`edullm_platform.checkpoints.success_marker_bytes` decides what a success
marker contains, and :func:`edullm_platform.checkpoints.inspect_checkpoint` decides what a
reader will accept. If the program carried an equivalent-looking copy, the two would agree
until one of them was edited and then disagree in a bucket, discovered by whoever next tried
to resume. Embedding the source means there is one definition and the copy cannot drift.

What is *not* solved here, and is recorded as a residual rather than hidden: the container
does not import ``edullm_platform``. The image carries OLMo-core, torch, boto3 and wandb, and
adding the platform package to it is a change to another repository's Dockerfile, a rebuild,
a scan exception and a job-definition re-pin. Extracting the one function by source is the
honest interim: it is the same bytes, and a test asserts the extraction still resolves.
"""

from __future__ import annotations

import argparse
import inspect
import json
import shlex
import subprocess
from pathlib import Path
from typing import Final

from edullm_platform.checkpoints import (
    CHECKSUM_ALGORITHM,
    MARKER_OBJECT,
    MARKER_SCHEMA_VERSION,
    success_marker_bytes,
)

#: The image the GPU job definition is pinned to. Named here so the form this tool writes
#: cannot drift from the definition that will run it; a submission naming a different digest
#: is refused at admission, which is the right answer but a slow way to learn it.
TRAINING_IMAGE_DIGEST: Final = (
    "sha256:e8f4d5aaea4c7a6e0f723f9b49fccf406ee63017baab4cea4e8b94b8e23e079f"
)

OLMO_CORE_CHECKOUT: Final = Path.home() / "projects-local" / "OLMo-core"

#: How far the run trains. Twenty steps on synthetic tokens is not a claim about learning
#: and is not dressed as one -- it is the smallest thing that exercises a real model, a real
#: optimizer and real device memory, which is what the phase is about.
TRAINING_STEPS: Final = 20


def marker_writer_source() -> str:
    """The platform's marker writer, as the text that goes into the program.

    ``inspect.getsource`` rather than a literal, so that a change to the marker's shape
    reaches the container without anybody remembering to copy it. The two names the function
    closes over travel with it; it needs nothing else from the package, which is what makes
    this extraction sound rather than merely convenient.
    """
    return f"MARKER_SCHEMA_VERSION = {MARKER_SCHEMA_VERSION}\n\n" + inspect.getsource(
        success_marker_bytes
    )


def training_program(*, steps: int = TRAINING_STEPS) -> str:
    """The program the container runs, with the platform's marker writer inside it.

    Every identity it uses comes from the environment the platform sets, never from this
    text: run id, team, output prefix and W&B project are all told to the container by
    ``batch_submit_request`` from the approved manifest. A project named here would be a
    submitter choosing their own attribution, which is the thing D4 exists to prevent.
    """
    return f'''
import hashlib, io, json, os, time
from datetime import datetime, timezone
from urllib.parse import urlparse

import boto3, torch, wandb
from olmo_core.data import TokenizerConfig
from olmo_core.nn.transformer import TransformerConfig

{marker_writer_source()}

# Both halves of the silent failure this phase is about, asserted before anything is spent.
# A CPU build reports no cuda version; a CUDA build with no device is a driver or a
# resourceRequirements problem. Either way the run stops here rather than training slowly
# and expensively on the wrong processor while looking entirely healthy.
assert torch.version.cuda, "torch is a CPU build"
assert torch.cuda.is_available(), "torch cannot see a CUDA device"

device = torch.device("cuda")
gpu = torch.cuda.get_device_name(0)
run_id = os.environ["EDULLM_RUN_ID"]
team = os.environ["EDULLM_TEAM"]
prefix = os.environ["EDULLM_OUTPUT_PREFIX"]

tokenizer = TokenizerConfig.gpt2()
vocab = tokenizer.padded_vocab_size()
model = TransformerConfig.olmo2_190M(vocab_size=vocab).build(init_device="cuda")
model.train()
parameters = sum(p.numel() for p in model.parameters())
optimizer = torch.optim.AdamW(model.parameters(), lr=1e-4)

tracker = wandb.init(
    project=os.environ["EDULLM_WANDB_PROJECT"],
    name=run_id,
    group=team,
    job_type="gpu-smoke",
    tags=["edullm", "phase-4", team],
    config={{
        "run_id": run_id,
        "team": team,
        "commit_sha": os.environ["EDULLM_COMMIT_SHA"],
        "dataset_release": os.environ["EDULLM_DATASET_RELEASE"],
        "gpu": gpu,
        "model": "olmo2_190M",
        "parameters": parameters,
        "steps": {steps},
    }},
)

# Synthetic tokens rather than a corpus. The claim being made is that this platform can run
# a real model through a real optimizer on a real GPU and keep the result; which corpus it
# read is a research question, and downloading one would spend GPU minutes on bandwidth.
generator = torch.Generator(device="cuda").manual_seed(0)
started = time.time()
losses = []
for step in range(1, {steps} + 1):
    ids = torch.randint(0, vocab, (2, 512), device=device, generator=generator)
    output = model(ids, labels=ids)
    output.loss.backward()
    optimizer.step()
    optimizer.zero_grad(set_to_none=True)
    loss = float(output.ce_loss)
    losses.append(loss)
    tracker.log({{"train/ce_loss": loss, "train/step": step}})
torch.cuda.synchronize()
elapsed = time.time() - started

# The checkpoint commit protocol: the payload, then the marker, in that order and never the
# other way round. A reader that finds the marker knows the object beside it is whole, which
# is what makes a checkpoint resumable rather than merely present. An interrupted write
# leaves the payload uncertified and correctly unusable.
buffer = io.BytesIO()
torch.save({{"step": {steps}, "model": model.state_dict()}}, buffer)
payload = buffer.getvalue()
digest = "sha256:" + hashlib.sha256(payload).hexdigest()

location = urlparse(prefix)
bucket = location.netloc
key = location.path.lstrip("/") + "checkpoints/step-{steps}/"
s3 = boto3.client("s3")
s3.put_object(
    Bucket=bucket,
    Key=key + "model.pt",
    Body=payload,
    ChecksumAlgorithm={CHECKSUM_ALGORITHM!r},
)
s3.put_object(
    Bucket=bucket,
    Key=key + {MARKER_OBJECT!r},
    Body=success_marker_bytes(
        step={steps},
        payload_name="model.pt",
        digest=digest,
        size_bytes=len(payload),
        created_at=datetime.now(timezone.utc),
    ),
    ChecksumAlgorithm={CHECKSUM_ALGORITHM!r},
)

# Read one object back. The workload role holds s3:GetObject on this prefix precisely so a
# resumed run can load what it wrote, and a grant nobody exercises is a grant nobody knows
# works.
marker = s3.get_object(Bucket=bucket, Key=key + {MARKER_OBJECT!r})["Body"].read().decode()

summary = {{
    "gpu": gpu,
    "torch": torch.__version__,
    "cuda": torch.version.cuda,
    "parameters": parameters,
    "steps": len(losses),
    "first_loss": round(losses[0], 4),
    "last_loss": round(losses[-1], 4),
    "seconds": round(elapsed, 2),
    "peak_memory_gib": round(torch.cuda.max_memory_allocated() / 2**30, 2),
    "checkpoint_uri": "s3://" + bucket + "/" + key,
    "checkpoint_sha256": digest,
    "checkpoint_bytes": len(payload),
    "success_marker": json.loads(marker),
    "wandb_url": tracker.url,
    "wandb_project": os.environ["EDULLM_WANDB_PROJECT"],
}}
print(json.dumps(summary, indent=2, sort_keys=True))
tracker.finish()

# The loss is not asserted to have fallen. Twenty steps on random tokens is not a claim about
# learning, and dressing it as one would be the kind of evidence this repository spends its
# time removing.
assert torch.cuda.max_memory_allocated() > 0, "nothing was ever allocated on the GPU"
'''


def dispatch_form(*, commit_sha: str, steps: int = TRAINING_STEPS) -> dict[str, str]:
    return {
        "repository": "OLMo-core",
        "commit_sha": commit_sha,
        "image_digest": TRAINING_IMAGE_DIGEST,
        "workload_profile": "olmo-core-gpu-smoke",
        "dataset_release": "dolma-2026-07",
        "team": "platform",
        "wandb_project": "edullm-platform-smoke",
        "maximum_runtime_hours": "0.5",
        "command": "python -c " + shlex.quote(training_program(steps=steps)),
    }


#: Which form fields the submission workflow reads as text and which as whole numbers. The
#: split is the workflow's, mirrored here so that a payload validated locally is the payload
#: the workflow would build from the same form.
TEXT_FIELDS: Final = (
    "repository",
    "commit_sha",
    "image_digest",
    "workload_profile",
    "dataset_release",
    "team",
    "wandb_project",
    "compute_profile",
    "maximum_runtime_hours",
    "fanout_index_parameter",
)
WHOLE_FIELDS: Final = ("maximum_attempts", "fanout_size", "fanout_parallelism")


def workflow_inputs(form: dict[str, str]) -> dict[str, object]:
    """The form as ``gh workflow run --json`` would deliver it, command already split."""
    inputs: dict[str, object] = {}
    for field in TEXT_FIELDS:
        value = str(form.get(field, "")).strip()
        if value:
            inputs[field] = value
    for field in WHOLE_FIELDS:
        value = str(form.get(field, "")).strip()
        if value:
            inputs[field] = int(value)
    command = shlex.split(str(form["command"]))
    if command:
        inputs["command"] = command
    return inputs


def head_of(checkout: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "origin/main"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--steps", type=int, default=TRAINING_STEPS)
    parser.add_argument(
        "--commit-sha",
        default=None,
        help=(
            "the OLMo-core commit the run declares. Read from the local checkout's "
            "origin/main when omitted, because the commit has to be one the admission "
            "validator can resolve on the remote."
        ),
    )
    parser.add_argument("--checkout", type=Path, default=OLMO_CORE_CHECKOUT)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    commit = arguments.commit_sha or head_of(arguments.checkout)
    form = dispatch_form(commit_sha=commit, steps=arguments.steps)
    inputs = workflow_inputs(form)
    arguments.output.write_text(json.dumps(inputs, indent=2, sort_keys=True) + "\n")

    program = training_program(steps=arguments.steps)
    command = inputs["command"]
    # The program must survive the round trip the workflow puts it through, or the container
    # runs something that merely parses.
    assert isinstance(command, list), "the command must reach the workflow as a list of words"
    assert command[:2] == ["python", "-c"], command[:2]
    assert command[-1] == program, "shlex did not round-trip the program"
    compile(program, "<training>", "exec")

    print(f"commit         {commit}")
    print(f"steps          {arguments.steps}")
    print(f"program bytes  {len(program)}")
    print(f"written        {arguments.output}")
    print("the program compiles and round-trips through shlex")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
