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
import ast
import inspect
import io
import json
import shlex
import subprocess
import tokenize
from pathlib import Path
from typing import Final

from edullm_platform.checkpoints import (
    CHECKSUM_ALGORITHM,
    MARKER_OBJECT,
    MARKER_SCHEMA_VERSION,
    success_marker_bytes,
)
from edullm_platform.config import load_yaml
from edullm_platform.contracts.execution import ExecutionTarget
from edullm_platform.contracts.manifest import RunManifest
from edullm_platform.execution import batch_submit_request, refuse_an_oversized_override

#: The digest the GPU job definitions are pinned to, kept only so this file can say why the
#: form does NOT name it. It is a placeholder: RegisterJobDefinition puts the manifest's own
#: digest into the per-run definition (execution.py), so the base definition's image is never
#: the image a run pulls.
#:
#: NAMING IT IN THE FORM WAS A REFUSAL WAITING TO HAPPEN, AND IT HAPPENED. compile_submission
#: requires the declared digest to be one the declared commit published, so a form carrying
#: this constant compiles only for whichever commit built it -- every other commit is refused
#: with "image digest ... was not published from commit ...". Measured 2026-08-04: this digest
#: is no longer in the registry at all, so the form could not compile for any commit.
#:
#: The form therefore leaves ``image_digest`` empty, which is what the workflow's own input
#: help asks for -- "Leave blank. The image comes from the commit above." -- and the
#: submission workflow resolves the commit's published image before compiling. ``--image-
#: digest`` pins one where a caller needs two dispatches to declare byte-identical manifests.
JOB_DEFINITION_PLACEHOLDER_DIGEST: Final = (
    "sha256:50e2488ab3c77e859a8fe3e6d4a06d7d54f5c852bc7c5dd201fb9db53bff455b"
)

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]

OLMO_CORE_CHECKOUT: Final = Path.home() / "projects-local" / "OLMo-core"

#: The bucket only the platform writes to. Named here so the probe that this role cannot
#: reach it has something to reach for.
LINEAGE_BUCKET: Final = "sbsandbox-intern-edullm-lineage"

#: How far the run trains. Twenty steps on synthetic tokens is not a claim about learning
#: and is not dressed as one -- it is the smallest thing that exercises a real model, a real
#: optimizer and real device memory, which is what the phase is about.
TRAINING_STEPS: Final = 20

#: The published tokenizers this platform can build an OLMo-core model for, keyed by the
#: dataset id a corpus names in its own ``groups[].depends_on[]`` entry with role
#: ``tokenizer``, and valued by the expression the training program evaluates.
#:
#: NOT A DEFAULT AND NOT A FALLBACK. A constant here would be right for one corpus and
#: silently wrong for another: a byte corpus read with a dolma2 vocab puts every id inside an
#: embedding sized 100,352, so nothing raises and the loss curve is merely bad. The upstream
#: family file turns its own family-wide tokenizer default off for exactly this reason, in
#: writing. So the corpus states its tokenizer, the registry carries it, `batch_submit_request`
#: sends it, and this map turns it into a config.
#:
#: ONE ENTRY, NOT TWO, AND THE MISSING ONE IS A MEASUREMENT RATHER THAN AN OVERSIGHT.
#: ``tokenizer/bytes-utf8`` is published and sealed and `pretrain/lean4-mathlib-bytes` depends
#: on it, but OLMo-core has no byte tokenizer: `TokenizerConfig` offers dolma2, dolma2_sigdig,
#: gpt_neox_olmo_dolma_v1_5, gpt2 and from_hf, and nothing under `olmo_core/data/` mentions
#: bytes or utf8 at all -- read from the checkout at OLMO_CORE_CHECKOUT on 2026-08-01 and
#: confirmed against that repository's main branch.
#:
#: `TokenizerConfig` is a plain dataclass, so `TokenizerConfig(vocab_size=..., eos_token_id=...,
#: pad_token_id=...)` would construct one. That is not done here, because the three numbers
#: are facts about a published tokenizer and only two of them are guessable: a 256-entry
#: vocabulary of raw bytes has no room for an end-of-sentence id, so whatever
#: `tokenizer/bytes-utf8` does about that is something to read out of its own tokenizer.json
#: rather than to infer. An invented eos id is the quiet kind of wrong this whole map exists
#: to refuse.
#:
#: THREE ENTRIES NOW, AND THE ARGUMENT ABOVE SURVIVES FOR ONE OF THEM AND NOT THE OTHER TWO.
#: The paragraph beginning "ONE ENTRY, NOT TWO" is still exactly right about
#: `tokenizer/bytes-utf8` and was wrong to be read as covering the two entries beside it. The
#: symmetry is the trap: every tokenizer missing from this map fails the same way, so a reader
#: who diagnosed one concluded the same about the rest, and that reading is what kept
#: `pretrain/fineweb-edu-1b` unusable and sent the diagnosis to `edullm-data` to fix manifests
#: that were never broken. Only some of them are missing upstream features.
#: `tokenizer/bytes-utf8` still is. The other two are not, and each names an exact OLMo-core
#: equivalent rather than one recognised from its name.
#:
#: `tokenizer/smollm2-bpe` has one: `s3://edullm-data/tokenizer/smollm2-bpe/v1/dataset.json`
#: names `HuggingFaceTB/SmolLM2-135M` as its source in those words, and `TokenizerConfig.from_hf`
#: reproduces it. Called for real before this line was written, it reports vocab_size 49,152.
#:
#: `tokenizer/qwen25-vendored` has one too --
#: `s3://edullm-data/tokenizer/qwen25-vendored/v1/dataset.json` names
#: `https://huggingface.co/Qwen/Qwen2.5-0.5B` in `sources[].uri`, so the identifier below is
#: read out of the published tokenizer rather than recognised from its name, and `from_hf`
#: reproduces it.
#:
#: CALLED FOR REAL BEFORE THIS LINE WAS WRITTEN, against the checkout at OLMO_CORE_CHECKOUT on
#: 2026-08-02, it reports vocab_size 151,936, eos_token_id 151,643, pad_token_id 151,643 and
#: bos_token_id 151,643. `from_hf` takes those off the Hub's config.json rather than off
#: anything in the bucket, so what had to be established is that the vendored copy and the Hub
#: agree about ids, and it was checked rather than assumed: the vendored `tokenizer.json`
#: holds 151,643 model entries plus 22 added tokens reaching 151,664, every one of those id
#: assignments is identical to the Hub's, and the merge list matches once the two
#: serialisations of it are normalised. The files are not byte identical -- upstream now
#: writes merges as pairs rather than space-joined strings and spells the ByteLevel decoder's
#: defaults explicitly -- and neither difference moves an id. Its `tokenizer_config.json`
#: names `<|endoftext|>` as both eos and pad, which its own added_tokens put at 151,643, so
#: the bucket and the Hub state the same eos independently.
#:
#: 151,936 IS LARGER THAN THE 151,665 IDS THAT EXIST, which is Qwen's own padding rather than
#: a mismatch, and it matters in the safe direction: every id the corpus can hold is inside
#: the embedding. It also puts this corpus past uint16, so unlike dolma2's 100,278 its ids
#: could not have been written narrower -- which changes nothing about the hazard this map
#: exists for, because a uint32 shard read at OLMo-core's default still decodes into ids this
#: vocabulary accepts. The dtype comes from the manifest and is never inferred.
#:
#: WHAT THIS ENTRY COSTS THAT THE DOLMA2 ONE DOES NOT. `from_hf` fetches the tokenizer's
#: config from the HuggingFace Hub when the config is built, so a corpus resolved through it
#: needs network egress at container runtime that a dolma2 corpus does not. Batch hosts sit in
#: public subnets with allow-all egress and the payload is a few hundred kilobytes, so this is
#: a real dependency rather than a real risk -- but a Hub outage can now refuse a run that
#: dolma2 would have started, and that is worth knowing before it happens.
#:
#: AND THE HAZARD THIS MAP EXISTS FOR APPLIES TO SmolLM2 TOO, AT A DIFFERENT NUMBER. SmolLM2's
#: 49,152 fits in uint16 exactly as dolma2's 100,278 does, so a fineweb shard read without the
#: manifest's explicit dtype would be decoded two bytes at a time into in-range ids and a loss
#: curve that is merely bad. The dtype comes from the manifest and is never inferred, which is
#: what makes adding this safe.
#:
#: THIS MAP MUST NOT LEAD OLMo-core's, AND WITH BOTH OF THESE LINES IT DOES. The container's
#: own copy lives at `OLMo-core/.edullm/train_on_corpus.py`, and on that repository's
#: `origin/main` -- commit d663baeb, fetched 2026-08-02 -- it holds `tokenizer/dolma2-bpe`
#: alone; the smollm2 entry exists there only on branch `edullm/smollm2-tokenizer`. So until a
#: matching line lands on that main, a submitter picking the corpus either entry unlocks
#: resolves it, reaches the container, and is refused at the tokenizer lookup with
#: THIS_IMAGE_HAS_NO_CONFIG_FOR_THAT_TOKENIZER and exit 69. That refusal is loud, immediate
#: and names the tokenizer, so it costs minutes rather than a GPU day -- but it is a refusal,
#: and the ordering is not optional: the OLMo-core change lands first and this ships after.
#: Landing them the other way round offers a corpus every image refuses.
TOKENIZERS: Final[dict[str, str]] = {
    "tokenizer/dolma2-bpe": "TokenizerConfig.dolma2()",
    "tokenizer/gigatoken-bpe": (
        "TokenizerConfig(vocab_size=100002, eos_token_id=100000, pad_token_id=100001, "
        "identifier=None)"
    ),
    "tokenizer/gigatoken-superbpe": (
        "TokenizerConfig(vocab_size=100002, eos_token_id=100000, pad_token_id=100001, "
        "identifier=None)"
    ),
    "tokenizer/qwen25-vendored": 'TokenizerConfig.from_hf("Qwen/Qwen2.5-0.5B")',
    "tokenizer/smollm2-bpe": 'TokenizerConfig.from_hf("HuggingFaceTB/SmolLM2-135M")',
}

def marker_writer_source() -> str:
    """The platform's marker writer and its checksum, as the text that goes into the program.

    ``inspect.getsource`` rather than a literal, so that a change to the marker's shape
    reaches the container without anybody remembering to copy it.

    THE CRC32C IS READ BACK RATHER THAN EMBEDDED, FOR TWO REASONS THAT AGREE. The write
    path now asks S3 to attest a CRC32C, so a marker recording only a SHA-256 leaves the
    reader nothing to compare the attestation against and the checkpoint reads as CORRUPT --
    the program has to record one. Embedding this module's ``crc32c`` was the obvious way and
    it is wrong twice over. It pushed the program past the 8,192 bytes Batch accepts for
    container overrides, which the size check caught; and a table-driven CRC in pure Python
    would run once per byte of a payload the size of a model, which is minutes of the GPU's
    time spent recomputing something boto3 has already computed in C.

    So the program takes the value out of the ``put_object`` response. What that gives up is
    an independent computation at write time -- but the client sent its own checksum with the
    upload and S3 rejects a mismatch, so the bytes are verified in transit regardless, and
    the reader's later comparison still catches a payload replaced after the fact.
    """
    return f"MARKER_SCHEMA_VERSION = {MARKER_SCHEMA_VERSION}\n\n" + inspect.getsource(
        success_marker_bytes
    )


def training_program(*, steps: int = TRAINING_STEPS, resume_from: str = "") -> str:
    """The program the container runs, with the platform's marker writer inside it.

    Every identity it uses comes from the environment the platform sets, never from this
    text: run id, team, output prefix and W&B project are all told to the container by
    ``batch_submit_request`` from the approved manifest. A project named here would be a
    submitter choosing their own attribution, which is the thing D4 exists to prevent.

    Two things beyond training, both of which need a container and cannot be established
    from a laptop.

    ``resume_from`` loads a previous run's checkpoint back into this run's model. Until
    something does, "a checkpoint is resumable" means the platform's reader will hand back a
    reference to it -- not that torch will accept the bytes. Those are different claims and
    only the second is what a researcher needs.

    The two isolation probes reach for things this role is refused: the outputs bucket
    listed from its root, past the ``s3:prefix`` condition on the ListBucket grant, and the
    lineage bucket, which the role holds no grant on at all. The workload role's trust
    policy names the Batch and ECS task services, so no human can assume it and be refused;
    the only principal that can be told no is a container. Without this, those two criteria
    rest on reading a policy document.

    **There were four, and two of them could not fail.** They aimed at a cross-team boundary
    the role no longer has -- ``infra/iam/batch-gpu-roles.yaml`` grants PutObject and
    GetObject unconditioned on ``teams/*/runs/*`` and records the decision to widen it -- so
    the probe key matched the grant. The write came back ``allowed`` and failed the run
    after the GPU had been paid for; the read came back ``NoSuchKey`` and passed while
    establishing nothing, which is precisely the distinction :func:`refusal_for` exists to
    insist on.
    """
    resume_block = (
        f'''
# RESUMING FROM A CHECKPOINT A DIFFERENT RUN WROTE, which is the claim that had not been
# tested. inspect_checkpoint establishes that the marker certifies the payload and that the
# store agrees with the marker; it says nothing about whether torch can load the result.
resumed = {{}}
resume_uri = {resume_from!r}
location = urlparse(resume_uri)
buffer = io.BytesIO()
s3 = boto3.client("s3")
s3.download_fileobj(location.netloc, location.path.lstrip("/"), buffer)
payload = buffer.getvalue()
restored = torch.load(io.BytesIO(payload), map_location="cuda", weights_only=True)
# load_state_dict is what makes this a resume rather than a download. strict=True so a
# checkpoint from a different architecture is refused here rather than producing a model
# that is silently part someone else's.
model.load_state_dict(restored["model"], strict=True)
resumed = {{
    "uri": resume_uri,
    "bytes": len(payload),
    "sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
    "step": restored["step"],
    "tensors": len(restored["model"]),
}}
print(json.dumps({{"resumed": resumed}}, sort_keys=True))
'''
        if resume_from
        else '\nresumed = {}\n'
    )

    return f'''
import base64, hashlib, io, json, os, time
from datetime import datetime, timezone
from urllib.parse import urlparse

import boto3, botocore, torch, wandb
from olmo_core.data import TokenizerConfig
from olmo_core.nn.transformer import TransformerConfig

{marker_writer_source()}


def refusal_for(call, **arguments):
    """What S3 said when this role reached for something, as a code rather than a boolean.

    THE CODE IS THE EVIDENCE AND A BOOLEAN WOULD NOT BE. AccessDenied means the role may not
    look. NoSuchKey or 404 means it may look and found nothing, which establishes no
    isolation at all -- and is exactly what a probe against a prefix that happens to be
    empty would return from a role permitting everything.
    """
    try:
        call(**arguments)
    except botocore.exceptions.ClientError as error:
        return error.response["Error"]["Code"]
    return "allowed"

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
{resume_block}

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
LN2 = 0.6931471805599453
started = time.time()
losses = []
# BYTES, NOT ARBITRARY IDS, AND THAT IS WHAT MAKES THE FIGURE BELOW A BITS-PER-BYTE ONE.
# Drawing from [0, 256) makes one token exactly one byte, so bpb is the loss in bits with no
# tokenizer to decode through, no network to reach the Hub over, and no conversion factor
# anybody had to invent. Ids above 255 never occur; the vocabulary, the parameter count and
# the memory footprint are unchanged.
#
# THE LABELS ARE SHIFTED HERE BECAUSE olmo_core DOES NOT SHIFT THEM, AND `labels=ids` IS THE
# ONE MISTAKE THIS PROGRAM CANNOT SURVIVE MAKING QUIETLY. `Transformer.forward` hands labels
# straight to the LM head; the shift lives in `olmo_core.data.utils.get_labels`, whose own
# comment reads "labels are just input IDs shifted to the left (first item is ignored)" and
# which the trainer calls on the caller's behalf. This program builds its batch itself, so
# nothing calls it, and unshifted labels ask the model to predict token t from a context that
# already contains token t. That is a copy, a causal model solves it in a handful of steps,
# and the loss it reports is not a language-modelling loss at all.
#
# Measured, on run_019fd2bc: twenty steps took bpb from 15.85 to 2.48 -- below the eight bits
# a uniform byte carries, which is the floor asserted at the bottom of this program and the
# only reason the mistake was ever found. It had never been reached before, because until
# botocore[crt] reached the image every run of this program died at the checkpoint write
# forty lines above it. The pad value is the -100 that `get_labels` uses, so the last
# position -- the one with no next token -- is ignored rather than predicted.
for step in range(1, {steps} + 1):
    ids = torch.randint(0, 256, (2, 512), device=device, generator=generator)
    labels = torch.nn.functional.pad(ids[..., 1:], (0, 1, 0, 0), value=-100)
    output = model(ids, labels=labels)
    output.loss.backward()
    optimizer.step()
    optimizer.zero_grad(set_to_none=True)
    loss = float(output.ce_loss)
    losses.append(loss)
    tracker.log({{"train/ce_loss": loss, "train/bpb": loss / LN2, "train/step": step}})
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
written = s3.put_object(
    Bucket=bucket,
    Key=key + "model.pt",
    Body=payload,
    ChecksumAlgorithm={CHECKSUM_ALGORITHM!r},
)
# The digest the store attests, which is the one the reader will compare the marker
# against. Taken from the response rather than recomputed here: boto3 already calculated
# it in C to send with the upload, and a pure-Python CRC over a payload this size would
# cost minutes of the GPU's time to arrive at the same number.
crc = "crc32c:" + base64.b64decode(written["ChecksumCRC32C"]).hex()
s3.put_object(
    Bucket=bucket,
    Key=key + {MARKER_OBJECT!r},
    Body=success_marker_bytes(
        step={steps},
        payload_name="model.pt",
        digest=digest,
        size_bytes=len(payload),
        created_at=datetime.now(timezone.utc),
        crc32c_digest=crc,
    ),
    ChecksumAlgorithm={CHECKSUM_ALGORITHM!r},
)

# Read one object back. The workload role holds s3:GetObject on this prefix precisely so a
# resumed run can load what it wrote, and a grant nobody exercises is a grant nobody knows
# works. The bytes are deliberately dropped: a missing grant raises ClientError here and
# fails the run, which is the whole of what this call establishes, and the marker's content
# is already an object in S3 that the platform's own checkpoint survey reads. Echoing it
# into the summary cost 60 bytes of a container override budget with 8,192 in it.
s3.get_object(Bucket=bucket, Key=key + {MARKER_OBJECT!r})["Body"].read()

# WHAT THIS ROLE CANNOT REACH, established from inside the only principal that can be
# refused. Two probes, where there were four. The other two aimed at a team boundary the
# role no longer has: infra/iam/batch-gpu-roles.yaml grants PutObject and GetObject
# unconditioned on teams/*/runs/*, which the old probe key matched -- so the write came back
# allowed and failed the run, and the read came back NoSuchKey and passed while proving
# nothing. That template records the decision to widen the role and says the cross-team
# check encodes no requirement.
#
# The two that remain still mean something. ListBucket on the outputs bucket carries a
# StringLike condition on s3:prefix, so listing from the root is refused; and nothing on this
# role reaches the lineage bucket at all, which is the property the whole write-once design
# rests on.
isolation = {{
    "list_the_whole_outputs_bucket": refusal_for(
        s3.list_objects_v2, Bucket=bucket, Prefix=""
    ),
    "write_to_the_lineage_bucket": refusal_for(
        s3.put_object,
        Bucket={LINEAGE_BUCKET!r},
        Key="result/" + run_id + ".json",
        Body=b"{{}}",
    ),
}}

summary = {{
    "isolation": isolation,
    "resumed": resumed,
    "gpu": gpu,
    "torch": torch.__version__,
    "cuda": torch.version.cuda,
    "parameters": parameters,
    "steps": len(losses),
    "first_loss": round(losses[0], 4),
    "last_loss": round(losses[-1], 4),
    "first_bpb": round(losses[0] / LN2, 4),
    "last_bpb": round(losses[-1] / LN2, 4),
    "bytes_scored": len(losses) * 1024,
    "seconds": round(elapsed, 2),
    "peak_memory_gib": round(torch.cuda.max_memory_allocated() / 2**30, 2),
    "checkpoint_uri": "s3://" + bucket + "/" + key,
    "checkpoint_sha256": digest,
    "checkpoint_bytes": len(payload),
    "wandb_url": tracker.url,
    "wandb_project": os.environ["EDULLM_WANDB_PROJECT"],
}}
print(json.dumps(summary, indent=2, sort_keys=True))
tracker.finish()

# The loss is still not asserted to have fallen: twenty steps on random bytes is not a claim
# about learning. What IS asserted is the floor underneath the figure. Uniform bytes carry
# eight bits each, so a run reporting below 8.0 over tokens it has never seen twice has
# computed something other than the thing the field is named after -- which is the failure
# that would otherwise reach a chart looking like a result.
assert torch.cuda.max_memory_allocated() > 0, "nothing was ever allocated on the GPU"
assert min(losses) / LN2 > 8.0, "bits per byte fell below the uniform-byte floor"
assert all(p.dtype is torch.float32 for p in model.parameters()), "this card has no bfloat16"

# The isolation probes ARE asserted, and the run fails if any of them came back allowed.
# Recording a refusal that did not happen would be worse than not probing: the capture would
# say the boundary holds, and the criterion would cite it.
reachable = sorted(name for name, code in isolation.items() if code == "allowed")
assert not reachable, "this role reached something it must not: " + ", ".join(reachable)
'''


def _measuring_target() -> ExecutionTarget:
    """A target that exists only to be measured, with the longest plausible ARNs.

    The override's size depends on the environment ``batch_submit_request`` adds, and that
    depends on the run id and the team rather than on anything here -- but building a real
    request is the only way to measure what the platform will actually send, rather than a
    reconstruction of it that can drift. The ARNs below are never used to submit anything.
    """
    account = "0" * 12
    return ExecutionTarget(
        compute_profile="gpu-1xa10g",
        region="us-east-1",
        job_queue_arn=f"arn:aws:batch:us-east-1:{account}:job-queue/sbsandbox-intern-edullm-gpu",
        job_definition_arn=(
            f"arn:aws:batch:us-east-1:{account}:job-definition/sbsandbox-intern-edullm-gpu-run"
        ),
        execution_role_arn=f"arn:aws:iam::{account}:role/sbsandbox-intern-edullm-batch-gpu-execution",
        workload_role_arn=f"arn:aws:iam::{account}:role/sbsandbox-intern-edullm-batch-gpu-workload",
        log_group="/aws/batch/sbsandbox-intern-edullm-gpu",
    )


def _without_comments(program: str) -> str:
    """Tokenised rather than pattern-matched. A ``#`` inside a string literal is not a
    comment, and the program contains several -- an S3 key fragment among them."""
    lines = program.splitlines(keepends=True)
    starts_at: dict[int, int] = {}
    for token in tokenize.generate_tokens(io.StringIO(program).readline):
        if token.type == tokenize.COMMENT:
            row, column = token.start
            starts_at[row] = min(starts_at.get(row, column), column)
    kept: list[str] = []
    for number, line in enumerate(lines, start=1):
        if number not in starts_at:
            kept.append(line)
            continue
        # A trailing comment leaves its code; a whole-line comment leaves nothing, and the
        # line goes with it rather than becoming a blank one.
        remainder = line[: starts_at[number]].rstrip()
        if remainder:
            kept.append(remainder + "\n")
    return "".join(kept)


def _without_docstrings(program: str) -> str:
    """The same removal, for the prose that is a statement rather than a comment.

    Parsed rather than matched, and by line span rather than by rewriting the tree:
    ``ast.unparse`` would reformat every line of the program, and what is wanted here is
    the program with some lines deleted.
    """
    tree = ast.parse(program)
    spans: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
            and first.end_lineno is not None
        ):
            spans.update(range(first.lineno, first.end_lineno + 1))
    return "".join(
        line
        for number, line in enumerate(program.splitlines(keepends=True), start=1)
        if number not in spans
    )


def for_the_wire(program: str) -> str:
    """The program with its comments and docstrings removed, which is what gets submitted.

    **THE 8,192-BYTE LIMIT IS NOT WHERE IT LOOKS, AND THIS FUNCTION IS WHAT PAYS FOR THAT.**
    Batch refuses a submission with "Container Overrides length must be at most 8192", and
    :func:`edullm_platform.execution.refuse_an_oversized_override` measures the serialized
    ``ContainerOverrides`` against that number. Measured 2026-08-04 against two refused
    submissions on ``gpu-1xt4``: the overrides that arrived were **7,680 bytes**, 512 inside
    the stated limit, and Batch refused them anyway. Reconstructing the whole ``SubmitJob``
    request -- JobName, the two ARNs, Tags, Timeout, RetryStrategy and PropagateTags around
    the same overrides -- gives **8,430**. The limit binds on the request, and the platform's
    guard is optimistic by roughly 750 bytes. That is a finding about the guard and is not
    fixed here; what is done here is fitting inside the real budget.

    Comments were the first 2,581 bytes to go and they were the right ones: they exist for
    somebody reading this file, and this file is what gets reviewed; nobody reads the command
    string in a Batch job description.

    **DOCSTRINGS NOW GO TOO, WHICH REVERSES A DECISION AND THE REASON IT WAS MADE IS WORTH
    ANSWERING RATHER THAN DELETING.** They were kept because
    :func:`edullm_platform.checkpoints.success_marker_bytes` travels inside this program by
    :func:`inspect.getsource`, and the property being protected was that what runs is the
    platform's function rather than a copy that can drift from it. That property survives.
    It is created by extracting the source instead of typing it out, and it is checked two
    ways that both still hold: the unstripped program is asserted to contain
    ``getsource(success_marker_bytes)`` exactly, and the embedded writer is *executed* and
    its bytes compared with the platform writer's. A docstring does not run, and stripping
    one is the same mechanical transformation already accepted for comments -- applied to
    text that was extracted, not authored, here.
    """
    return _without_docstrings(_without_comments(program))


def dispatch_form(
    *,
    commit_sha: str,
    steps: int = TRAINING_STEPS,
    resume_from: str = "",
    image_digest: str = "",
) -> dict[str, str]:
    return {
        "repository": "OLMo-core",
        "commit_sha": commit_sha,
        # Empty, so the submission workflow resolves the image the declared commit
        # published. See JOB_DEFINITION_PLACEHOLDER_DIGEST for what naming one cost.
        "image_digest": image_digest,
        "workload_profile": "olmo-core-check",
        # THE CHEAPEST CARD IN THE CATALOG, AND THE ONE THE SPINE IS PROVEN ON. Its queue is
        # one the deployed admission-states role can submit to, its compute environment is
        # enabled on g4dn.xlarge, and its job definition exists -- all read off the account
        # rather than assumed. The T4 has no hardware bfloat16 and this program is fp32,
        # which the container now asserts.
        #
        # `none` rather than dolma-2026-07: config/datasets.yaml retires that release and the
        # form's dataset_release is a `choice` input, so a dispatch naming it is rejected by
        # GitHub before anything compiles. This program reads no corpus, so `none` is also
        # the true answer.
        "compute_profile": "gpu-1xt4",
        "dataset_release": "none",
        "team": "platform",
        "wandb_project": "edullm-platform-smoke",
        "maximum_runtime_hours": "0.5",
        "command": "python -c "
        + shlex.quote(for_the_wire(training_program(steps=steps, resume_from=resume_from))),
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
WHOLE_FIELDS: Final = ("maximum_attempts", "fanout_size")


def dispatch_inputs(form: dict[str, str]) -> dict[str, str]:
    """What ``gh workflow run --json`` is given. Every value a string, including the command.

    THIS AND :func:`workflow_inputs` ARE DIFFERENT SHAPES AND CONFLATING THEM COSTS A
    DISPATCH. ``workflow_dispatch`` declares every input as ``type: string``, so a JSON array
    is refused before the run starts -- "cannot unmarshal array into Go value of type
    string", which reads like a malformed payload rather than a field of the wrong type.

    The command is therefore one shell command line, POSIX-quoted, and the workflow splits
    it on the runner. That split is the workflow's and is mirrored in
    :func:`workflow_inputs` so the payload can be validated here against what will actually
    be built there.
    """
    return {
        field: str(form[field]).strip()
        for field in (*TEXT_FIELDS, *WHOLE_FIELDS, "command")
        if str(form.get(field, "")).strip()
    }


def workflow_inputs(form: dict[str, str]) -> dict[str, object]:
    """The form the *workflow* assembles from those inputs, with the command already split.

    Mirrors the inline script in ``.github/workflows/submit-run.yml``: text fields stripped
    and dropped when empty, the two bounds parsed as whole numbers, and the command run
    through ``shlex.split``. It exists so a payload can be validated against
    ``SubmissionInputs`` on a laptop rather than discovered to be wrong by a runner.
    """
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
    parser.add_argument(
        "--image-digest",
        default="",
        help=(
            "pin the image rather than letting the workflow resolve it from the commit. "
            "Empty is the ordinary answer. Give one where two dispatches have to declare "
            "byte-identical manifests and a rebuild between them would be a difference."
        ),
    )
    parser.add_argument(
        "--resume-from",
        default="",
        help=(
            "an s3:// URI of a checkpoint payload a previous run wrote. Given, the program "
            "loads it back into this run's model before training, which is the only way to "
            "establish that a checkpoint is resumable rather than merely certified."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    commit = arguments.commit_sha or head_of(arguments.checkout)
    form = dispatch_form(
        commit_sha=commit,
        steps=arguments.steps,
        resume_from=arguments.resume_from,
        image_digest=arguments.image_digest,
    )
    # The dispatch payload is what gets written; the split form is only ever used to
    # check it here. Writing the split one was a real defect: gh refused it, and the
    # message named the JSON rather than the field.
    arguments.output.write_text(
        json.dumps(dispatch_inputs(form), indent=2, sort_keys=True) + "\n"
    )

    program = for_the_wire(
        training_program(steps=arguments.steps, resume_from=arguments.resume_from)
    )
    command = workflow_inputs(form)["command"]
    # The program must survive the round trip the workflow puts it through, or the container
    # runs something that merely parses.
    assert isinstance(command, list), "the command must reach the workflow as a list of words"
    assert command[:2] == ["python", "-c"], command[:2]
    assert command[-1] == program, "shlex did not round-trip the program"
    compile(program, "<training>", "exec")

    # The same refusal the platform applies, applied here, so an oversized submission costs
    # a local error rather than a dispatch, an approval and a Batch rejection.
    measuring_target = _measuring_target()
    refuse_an_oversized_override(
        batch_submit_request(
            manifest=load_yaml(
                PROJECT_ROOT / "fixtures" / "manifests" / "gpu-routine.yaml", RunManifest
            ).model_copy(update={"command": tuple(command)}),
            target=measuring_target,
            run_id="run_" + "0" * 36,
            # Whichever definition a run is submitted against, the override this measures
            # is the same size: the ARN is a sibling of ContainerOverrides and not inside
            # the budget. The target's own is passed because it is the one that exists.
            job_definition=measuring_target.job_definition_arn,
        )["ContainerOverrides"]
    )

    print(f"commit         {commit}")
    print(f"steps          {arguments.steps}")
    print(f"resume from    {arguments.resume_from or '(nothing)'}")
    print(f"program bytes  {len(program)} on the wire, "
          f"{len(training_program(steps=arguments.steps, resume_from=arguments.resume_from))} as written")
    print(f"written        {arguments.output}")
    print(f"dispatch with  gh workflow run submit-run.yml --ref main --json < {arguments.output}")
    print("the program compiles and round-trips through shlex")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
