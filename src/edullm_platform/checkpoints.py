"""The checkpoint commit protocol: what makes a written checkpoint a resumable one.

``CheckpointManifest`` has modelled the answer since Phase 0 -- a checkpoint is resumable
when a success marker sits beside it -- and until now nothing produced one. The first GPU
training run implemented the protocol inline, in a program passed as the value of a form
field, and got it right; but a protocol that lives in a submission is a protocol the next
submission can get wrong, and nothing would notice. This module is that protocol as code
two checks can be written against.

**Payload first, then the marker, and never the other way round.** That ordering is the
entire mechanism. A commit interrupted between the two leaves a payload nobody has
certified, which reads as unusable and is; reverse the order and an interruption leaves a
marker certifying a payload that was never written, which reads as resumable and is not.
Everything else here exists to make that one invariant survive contact with retries.

**The writes are unconditional, and that is deliberate -- the opposite of the lineage
store.** A lineage record is a statement about an instant that already passed, so a second
write of the same key is a redelivery and refusing it is correct. A checkpoint is not: a
retried attempt legitimately writes step 20 again, because the first attempt's step 20 died
before it was certified. Under a write-once rule the retry's payload would be refused, the
retry's marker would be written anyway, and the marker would then certify bytes from a dead
attempt. Fail-closed, but by losing a good checkpoint and keeping a bad one.

**What makes the unconditional write safe is that the reader verifies rather than trusts.**
The marker carries the digest of the payload it certifies; S3, asked at write time, carries
its own digest of the bytes it received. :func:`inspect_checkpoint` compares them, so a
marker and a payload that came from different attempts are :data:`CheckpointState.CORRUPT`
rather than resumable. This is strictly stronger than write-once, because it also catches a
payload that was replaced by something else entirely.

**Immutability is a property of the reference, not of the object.** ``CheckpointRef``
carries a digest, so a reference whose bytes have since changed is refused when it is used.
That is what "a checkpoint cannot silently become different bytes" means here, and it does
not require the object to be unwritable.

boto3 is not a project dependency -- it is in the Lambda runtime and in the training image,
and adding it to ``pyproject.toml`` would put the whole SDK into the admission validator's
zip. So the four calls this module makes are described by a Protocol, and the one error it
must recognise is recognised by the shape of its response rather than by its class, which
is the discipline :mod:`edullm_platform.lifecycle_handler` already uses.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Final, Protocol
from urllib.parse import urlparse

from edullm_platform.contracts.results import CheckpointManifest

__all__ = [
    "CHECKSUM_ALGORITHM",
    "FULL_OBJECT_CHECKSUM",
    "LEGACY_CHECKSUM_ALGORITHM",
    "MARKER_OBJECT",
    "MARKER_SCHEMA_VERSION",
    "MISSING_OBJECT_CODES",
    "OLMO_CORE_FULL_CHECKPOINT",
    "OLMO_CORE_STEP_DIRECTORY",
    "OLMO_CORE_WEIGHTS_ONLY",
    "CheckpointInspection",
    "CheckpointState",
    "CheckpointStore",
    "UnreadableCheckpointError",
    "commit_checkpoint",
    "crc32c",
    "described_checksum",
    "inspect_checkpoint",
    "olmo_core_checkpoint_shape",
    "resumable_checkpoint",
    "success_marker_bytes",
]

#: The one object whose presence means "the payload beside me is whole". Spelled here
#: because a reader and a writer that each chose their own name would agree until one of
#: them was edited, and the failure would be a checkpoint that silently stopped resuming.
MARKER_OBJECT: Final = "_SUCCESS"

MARKER_SCHEMA_VERSION: Final = 1

#: Asks S3 to compute and store a checksum over the bytes it received. Omitting it costs
#: nothing at write time, is invisible in every response, and produces an object that reads
#: exactly like an attested one until a reader asks for the digest and finds no field. Here
#: it is load-bearing rather than merely good practice -- without the store's own digest
#: there is nothing to compare the marker's claim against, and the reader degrades to
#: trusting whatever the marker says.
#:
#: CRC32C RATHER THAN SHA-256, AND THE REASON IS NOT PREFERENCE. For a multipart upload S3
#: supports SHA-256 as a *composite* checksum only -- the digest of concatenated part
#: digests, which is not the digest of the object. Full-object checksums on multipart are
#: available for CRC-32, CRC-32C and CRC-64/NVME and for nothing else; confirmed against
#: the S3 documentation on 2026-07-31. ``_attested_digest`` refuses a composite value on
#: purpose, so with SHA-256 on the write path every checkpoint large enough for a managed
#: transfer to go multipart -- above 8 MB, by boto3's default -- reads as CORRUPT. Nothing
#: noticed because the only checkpoint the platform has written came from a demo that holds
#: the whole payload in memory and calls ``put_object``, which is a single part.
CHECKSUM_ALGORITHM: Final = "CRC32C"

#: What the historical write path asked for, still read and no longer written. The Phase 4
#: evidence records a real checkpoint attested this way, and it is in a committed proof
#: bundle -- a reader that stopped understanding it would invalidate the only checkpoint
#: this platform has ever actually produced.
LEGACY_CHECKSUM_ALGORITHM: Final = "SHA256"

#: What S3 calls a digest over the whole object, as against ``COMPOSITE`` -- the digest of
#: concatenated part digests that a multipart upload produces. A composite value is not the
#: SHA-256 of the object and comparing one against the marker's claim would fail every
#: time, so it is refused with its own reason rather than reported as corruption.
FULL_OBJECT_CHECKSUM: Final = "FULL_OBJECT"

#: What S3 answers when the key is not there. Both spellings, plus the bare status: the
#: REST error for GetObject is ``NoSuchKey`` and for HeadObject it is ``404``/``NotFound``,
#: and recognising one and not the others would report an absent marker as an outage.
MISSING_OBJECT_CODES: Final = frozenset({"NoSuchKey", "NotFound", "404"})


#: Castagnoli's polynomial, reflected. CRC-32C is not in the standard library -- ``zlib.crc32``
#: is CRC-32, a different polynomial producing a different value -- and the alternatives are
#: ``google-crc32c`` or reaching into ``botocore``. Both are dependencies, and this module is
#: dependency-free on purpose: it is imported by the admission validator, whose zip is the
#: thing the release procedure exists to keep small. Sixteen lines of table-driven CRC is a
#: smaller cost than either, and it is checkable against a published constant.
_CRC32C_POLYNOMIAL: Final = 0x82F63B78


def _crc32c_table() -> tuple[int, ...]:
    table = []
    for byte in range(256):
        value = byte
        for _ in range(8):
            value = (value >> 1) ^ (_CRC32C_POLYNOMIAL if value & 1 else 0)
        table.append(value)
    return tuple(table)


_CRC32C_TABLE: Final = _crc32c_table()


def crc32c(data: bytes) -> int:
    """CRC-32C over ``data``, as the 32-bit value S3 base64-encodes into ``ChecksumCRC32C``.

    Verified against the published check value: CRC-32C of ``b"123456789"`` is 0xE3069283.
    That constant is what makes a hand-rolled implementation defensible rather than a place
    for a silent arithmetic mistake to live, and there is a test that asserts it.
    """
    crc = 0xFFFFFFFF
    for byte in data:
        crc = _CRC32C_TABLE[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF


def described_checksum(entries: Sequence[tuple[str, int, str]]) -> str:
    """A SHA-256 over a canonical description of what the verifier found.

    ``CheckpointManifest.checksum`` is typed ``Sha256Digest``, and the digest S3 attests is
    now usually a CRC32C, which cannot go in that field. The tempting fixes are both bad:
    widening the contract moves a recorded structural digest for a field nobody reads as bytes,
    and storing a CRC32C in a field named and patterned for a SHA-256 satisfies the type
    only by lying about it.

    So the field carries a SHA-256 of bytes this module composed -- the sorted
    ``(key, size, attestation)`` of everything verified -- which is honest about what it is
    and has the property the field is for: it changes when anything under the prefix
    changes. Serialised the way :func:`success_marker_bytes` serialises, so two verifiers
    that found the same thing produce the same value.
    """
    described = [
        {"key": key, "bytes": size, "attestation": attestation}
        for key, size, attestation in sorted(entries)
    ]
    payload = json.dumps(described, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


class UnreadableCheckpointError(ValueError):
    """The store answered, and the answer cannot be interpreted as a checkpoint."""


class CheckpointStore(Protocol):
    """The four S3 calls this module makes, described so mypy has something to check.

    A test supplies its own implementation and gets the same code path a container takes,
    rather than a branch that only exists for tests.
    """

    def put_object(self, **arguments: Any) -> Any: ...

    def head_object(self, **arguments: Any) -> Any: ...

    def get_object(self, **arguments: Any) -> Any: ...

    def list_objects_v2(self, **arguments: Any) -> Any: ...


class CheckpointState(StrEnum):
    """What is at a checkpoint prefix, named finely enough to act on.

    ``ABSENT`` and ``UNCOMMITTED`` are both "do not resume from this", and collapsing them
    would be a mistake a resume path pays for later: nothing there is an ordinary first
    run, whereas a payload with no marker is a run that got most of the way and died, and
    the second is worth saying out loud in a log where the first is noise.

    ``FOREIGN`` IS THE ONE THAT IS NOT ABOUT THIS PLATFORM'S OWN WRITES, AND IT WAS ADDED
    AFTER A RUN THAT SAVED 200 MB WAS REPORTED AS HAVING SAVED NOTHING. Every other member
    describes an OLMo-core checkpoint at some stage of being written. This one says the
    prefix holds a complete checkpoint that a different trainer wrote, which on this
    platform means a HuggingFace ``Trainer`` and is most of what post-training runs.

    Collapsing it into ABSENT is the bug it exists to fix: the reader said "nothing is
    stored at this prefix" about a directory holding a 48 MB adapter and a 96 MB optimizer
    state, and the nightly then told the researcher they had forgotten ``--save-folder``.
    Collapsing it into UNCOMMITTED would be the opposite error, because that word means a
    write that did not finish and a resume from it starts at step zero -- where a
    HuggingFace checkpoint resumes perfectly well, just not through OLMo-core's loader.
    And COMMITTED is reserved for what ``Trainer.fit()`` will load, so claiming it here
    would tell a retry it is continuing when it would be starting over.
    """

    ABSENT = "absent"
    UNCOMMITTED = "uncommitted"
    ORPHANED = "orphaned"
    CORRUPT = "corrupt"
    FOREIGN = "foreign"
    COMMITTED = "committed"


@dataclass(frozen=True)
class CheckpointInspection:
    """What one prefix holds, and a manifest only when it holds a resumable checkpoint.

    ``manifest`` is populated for :data:`CheckpointState.COMMITTED` and for nothing else,
    which is the code-level statement of the check that a checkpoint is resumable only
    once its marker exists. There is no path through :func:`inspect_checkpoint` that
    returns a manifest for a prefix whose marker is missing, so the check cannot be
    satisfied by a caller remembering to test the state first.
    """

    prefix: str
    state: CheckpointState
    detail: str
    manifest: CheckpointManifest | None = None

    @property
    def is_resumable(self) -> bool:
        return self.state is CheckpointState.COMMITTED


def _split(uri: str) -> tuple[str, str]:
    location = urlparse(uri)
    if location.scheme != "s3" or not location.netloc:
        raise UnreadableCheckpointError(f"a checkpoint prefix must be an s3:// URI, not {uri!r}")
    key = location.path.lstrip("/")
    if not key.endswith("/"):
        raise UnreadableCheckpointError(f"a checkpoint prefix must end in a slash, not {uri!r}")
    return location.netloc, key


def _error_code(error: BaseException) -> str | None:
    """The code S3 returned, read off the response because botocore is not importable."""
    response = getattr(error, "response", None)
    if not isinstance(response, Mapping):
        return None
    detail = response.get("Error")
    code = detail.get("Code") if isinstance(detail, Mapping) else None
    if isinstance(code, str):
        return code
    metadata = response.get("ResponseMetadata")
    status = metadata.get("HTTPStatusCode") if isinstance(metadata, Mapping) else None
    return str(status) if status is not None else None


def _is_missing(error: BaseException) -> bool:
    code = _error_code(error)
    return code is not None and code in MISSING_OBJECT_CODES


#: Which head field carries which algorithm's value, and how many bytes that value is. CRC32C
#: is tried first because it is what this module now writes; SHA-256 is second because it is
#: what the historical checkpoint carries. An object attested with both -- which S3 permits --
#: is read as CRC32C, and the marker carries both, so the comparison still has a counterpart.
_ATTESTATION_FIELDS: Final = (
    ("crc32c", "ChecksumCRC32C", 4),
    ("sha256", "ChecksumSHA256", hashlib.sha256().digest_size),
)


def _attested_digest(head: Mapping[str, Any]) -> str | None:
    """The digest S3 says it computed, as ``<algorithm>:<hex>``, or None if it said nothing.

    None rather than a guess when the object carries no whole-object checksum. That is
    either an object written without ``ChecksumAlgorithm`` or one assembled from parts, and
    in both cases there is no attestation to compare the marker against -- which the caller
    has to be able to distinguish from an attestation that disagrees.

    The algorithm travels with the value because the two sides of the comparison are chosen
    independently: S3 attests whatever the writer asked for, and the marker carries every
    digest the writer computed. Returning a bare hex string would let a CRC32C attestation
    be compared against a SHA-256 claim and reported as corruption, which is the same
    false alarm as reporting an unattested object -- loud, wrong, and sends a resuming run
    back to step zero.
    """
    if head.get("ChecksumType") not in (None, FULL_OBJECT_CHECKSUM):
        return None
    for algorithm, field, size in _ATTESTATION_FIELDS:
        encoded = head.get(field)
        if not isinstance(encoded, str) or not encoded:
            continue
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            continue
        if len(raw) != size:
            continue
        return f"{algorithm}:{raw.hex()}"
    return None


def _normalise_digest(claimed: object, *, algorithm: str = "sha256") -> str | None:
    """The marker's claim as ``<algorithm>:<hex>``, accepting the bare hex it may carry.

    The bare form is accepted because the first GPU training run wrote one, months before
    this module existed, and refusing it would mean this reader cannot read the only real
    checkpoint the platform has produced. Everything :func:`commit_checkpoint` writes
    carries the prefix, so the tolerance is a compatibility path and not a second format.
    """
    if not isinstance(claimed, str):
        return None
    size = next(
        (width for name, _, width in _ATTESTATION_FIELDS if name == algorithm),
        None,
    )
    if size is None:
        return None
    text = claimed.removeprefix(f"{algorithm}:").strip().lower()
    if len(text) != size * 2:
        return None
    try:
        bytes.fromhex(text)
    except ValueError:
        return None
    return f"{algorithm}:{text}"


def success_marker_bytes(
    *,
    step: int,
    payload_name: str,
    digest: str,
    size_bytes: int,
    created_at: datetime,
    epoch: int | None = None,
    crc32c_digest: str | None = None,
) -> bytes:
    """The marker's content, as the bytes that go into the object.

    Sorted keys and a compact separator so that committing the same checkpoint twice
    produces byte-identical markers, which is what lets a reader compare two attempts'
    output rather than only their digests.

    ``payload`` is named rather than implied. A marker that did not say what it certified
    would leave the reader to guess, and the guess -- "the one other object here" -- stops
    being right the moment a checkpoint is sharded across files.

    BOTH DIGESTS ARE RECORDED, AND NEITHER IS REDUNDANT. The reader compares the marker
    against whatever S3 attests, and which algorithm that is depends on how the object was
    written: this module now asks for CRC32C, the historical checkpoint carries SHA-256, and
    an object written by some other tool may carry either. A marker holding only the digest
    the writer happened to prefer would be unverifiable against a store that attested the
    other one -- reported as corruption, which is a good checkpoint refused. ``sha256`` also
    stays because ``CheckpointManifest.checksum`` is typed to it.
    """
    marker = {
        "schema_version": MARKER_SCHEMA_VERSION,
        "step": step,
        "payload": payload_name,
        "sha256": digest,
        "bytes": size_bytes,
        "created_at": created_at.isoformat(),
    }
    if crc32c_digest is not None:
        marker["crc32c"] = crc32c_digest
    if epoch is not None:
        marker["epoch"] = epoch
    return json.dumps(marker, sort_keys=True, separators=(",", ":")).encode()


def commit_checkpoint(
    store: CheckpointStore,
    *,
    prefix: str,
    step: int,
    payload: bytes,
    created_at: datetime,
    payload_name: str = "model.pt",
    epoch: int | None = None,
) -> CheckpointManifest:
    """Write the payload, then the marker that certifies it, and describe the result.

    Returns the manifest rather than writing it anywhere. Where a manifest belongs is a
    lineage question -- it goes in the ``ResultManifest`` the recorder writes -- and a
    function that both committed a checkpoint and wrote a lineage record would need the
    workload role to hold ``s3:PutObject`` on the lineage bucket, which is the one grant
    the whole lineage design exists to withhold.

    ``created_at`` is a parameter and not a clock. A checkpoint's timestamp is genuinely
    "when this was written", so a clock would be defensible here in a way it is not in the
    projection -- but a function with a clock in it cannot be tested for the bytes it
    produces, and the marker's bytes are the thing two attempts have to be able to agree
    on.
    """
    if not payload:
        raise UnreadableCheckpointError("a checkpoint with no payload certifies nothing")
    bucket, key = _split(prefix)
    digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"
    crc32c_digest = f"crc32c:{crc32c(payload).to_bytes(4, 'big').hex()}"

    # THE ORDER IS THE PROTOCOL. Anything between these two calls -- a spot reclaim, an OOM
    # kill, a network partition -- leaves a payload with no marker, which the reader reports
    # as UNCOMMITTED and refuses to resume from. Swapping them leaves a marker with no
    # payload, which is the same interruption reported as a checkpoint that exists.
    store.put_object(
        Bucket=bucket,
        Key=key + payload_name,
        Body=payload,
        ChecksumAlgorithm=CHECKSUM_ALGORITHM,
    )
    store.put_object(
        Bucket=bucket,
        Key=key + MARKER_OBJECT,
        Body=success_marker_bytes(
            step=step,
            payload_name=payload_name,
            digest=digest,
            size_bytes=len(payload),
            created_at=created_at,
            epoch=epoch,
            crc32c_digest=crc32c_digest,
        ),
        ChecksumAlgorithm=CHECKSUM_ALGORITHM,
    )
    return CheckpointManifest(
        schema_version=1,
        uri=prefix,
        step=step,
        epoch=epoch,
        created_at=created_at,
        size_bytes=len(payload),
        checksum=digest,
        success_marker_uri=prefix + MARKER_OBJECT,
    )


def _read_marker(store: CheckpointStore, *, bucket: str, key: str) -> Mapping[str, Any] | None:
    try:
        answer = store.get_object(Bucket=bucket, Key=key + MARKER_OBJECT)
    except Exception as error:
        # Broad because botocore's classes are not importable here, and narrowed at once:
        # a missing marker is an ordinary answer and anything else is re-raised unchanged,
        # because a reader that reported an outage as "no marker" would send a resuming job
        # back to step zero on a bad afternoon.
        if _is_missing(error):
            return None
        raise
    body = answer["Body"].read()
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as error:
        raise UnreadableCheckpointError(
            f"the success marker at s3://{bucket}/{key}{MARKER_OBJECT} is not JSON"
        ) from error
    if not isinstance(parsed, Mapping):
        raise UnreadableCheckpointError(
            f"the success marker at s3://{bucket}/{key}{MARKER_OBJECT} is not an object"
        )
    return parsed


#: What OLMo-core's own ``Checkpointer.dir_is_checkpoint`` accepts, read out of
#: ``src/olmo_core/train/checkpoint.py`` rather than inferred. Two shapes, and the
#: difference between them is not cosmetic.
#:
#: ``.metadata`` alone is model state and possibly optimizer state, with no trainer state --
#: a resume from it restores weights and starts the trainer cold. All three of the second
#: group is a full checkpoint the trainer continues from. A verifier that answered only
#: "committed" would let a twelve-hour run's second attempt believe it was continuing when
#: it was starting over with warm weights, which is the same class of wrongness as telling
#: everybody their checkpoints lose the optimizer when only the demo's do.
OLMO_CORE_WEIGHTS_ONLY: Final = (".metadata",)
OLMO_CORE_FULL_CHECKPOINT: Final = (
    "train/rank0.pt",
    "model_and_optim/.metadata",
    ".metadata.json",
)

#: Checkpoint directories are named ``step{N}``; OLMo-core reads the step off the name, and
#: so does this, because a directory carries no marker of ours to read one from.
OLMO_CORE_STEP_DIRECTORY: Final = re.compile(r"^step(\d+)$")

#: What HuggingFace's ``Trainer`` calls the same thing, which is the other trainer this
#: platform actually runs.
#:
#: A SECOND PATTERN RATHER THAN A WIDER FIRST ONE, AND THE DIFFERENCE IS THE WHOLE POINT.
#: Recognising a checkpoint here is two tests, the directory name and then the contents, and
#: only the name is shared between the two frameworks. Widening ``OLMO_CORE_STEP_DIRECTORY``
#: to admit ``checkpoint-32`` would send a HuggingFace directory into
#: :func:`olmo_core_checkpoint_shape`, which would correctly reject its contents, and the
#: prefix would be reported UNCOMMITTED -- "the write that produced it did not finish" --
#: about a checkpoint that finished and is loadable. That is the same false statement the
#: old ABSENT gave, reworded.
#:
#: THE OTHER WAY ROUND WAS TRIED FIRST AND IS WORSE. Renaming the uploaded directories to
#: ``step{N}`` was the cheap fix and it does not work, because the contents test still fails.
#: Forcing that test to pass by writing a ``.metadata`` file beside the adapter would
#: satisfy ``OLMO_CORE_WEIGHTS_ONLY`` and make this module report a checkpoint OLMo-core can
#: resume from, which it cannot. The comment above those constants names that exact failure.
HUGGINGFACE_CHECKPOINT_DIRECTORY: Final = re.compile(r"^checkpoint-(\d+)$")

#: What a HuggingFace checkpoint has to hold for its own trainer to resume step and epoch.
#:
#: ``Trainer._init_training_state`` gates the whole restore on
#: ``os.path.isfile(os.path.join(resume_from_checkpoint, TRAINER_STATE_NAME))``, so this one
#: file is the difference between continuing and starting over with warm weights. It is the
#: same distinction ``OLMO_CORE_FULL_CHECKPOINT`` draws against ``OLMO_CORE_WEIGHTS_ONLY``,
#: at a different filename.
HUGGINGFACE_TRAINER_STATE: Final = "trainer_state.json"

#: The optimizer state, which v5 of the library stops writing by default.
#:
#: Worth naming separately rather than folding into the full-checkpoint set, because
#: ``Trainer`` saves "excluding optimizer state by default" as of transformers 5, so the
#: ordinary shape of a modern HuggingFace checkpoint restores the step counter and starts
#: the optimizer cold. A workload profile declaring ``resume_required`` against one of these
#: is getting less than the word implies, and a reader has to be able to see which.
HUGGINGFACE_OPTIMIZER: Final = "optimizer.pt"

#: Every filename the library uses for the weights themselves, sharded or whole, full
#: fine-tune or PEFT adapter. Any one of them present means weights are here.
HUGGINGFACE_WEIGHTS: Final = (
    "model.safetensors",
    "model.safetensors.index.json",
    "pytorch_model.bin",
    "pytorch_model.bin.index.json",
    "adapter_model.safetensors",
    "adapter_model.bin",
)


def olmo_core_checkpoint_shape(names: Iterable[str]) -> str | None:
    """Which of OLMo-core's two accepted shapes these object names form, if either.

    ``names`` are the keys under one ``step{N}`` directory, relative to it.

    THE GUARANTEE THIS GIVES IS WEAKER THAN THE MARKER PROTOCOL'S AND THAT IS THE TRADE.
    A ``_SUCCESS`` marker says "these are the exact bytes that were committed". This says
    "the library will accept this as a checkpoint". The alternative was to have OLMo-core
    write our marker, which needs a callback after every checkpoint write -- code in a form
    field on every submission, which is the precise thing this module exists to stop -- and
    would produce a marker certifying a directory the library goes on writing and pruning
    without consulting it.

    The weaker guarantee is the one the question actually needs. A resuming attempt is not
    asking whether the bytes are unchanged since some earlier instant; nothing rewrites
    them, S3 validates each part's checksum on upload, and no attempt of the same run
    writes another attempt's step directory. It is asking whether ``Trainer.fit()`` will
    load this. A half-written directory from a reclaimed attempt fails ``dir_is_checkpoint``
    for the same reason it fails the loader, which is the failure the marker protocol was
    written for and the one case this still covers.
    """
    present = set(names)
    if all(required in present for required in OLMO_CORE_FULL_CHECKPOINT):
        return "model, optimizer and trainer state"
    if all(required in present for required in OLMO_CORE_WEIGHTS_ONLY):
        return "model state and possibly optimizer state, but no trainer state"
    return None


def huggingface_checkpoint_shape(names: Iterable[str]) -> str | None:
    """What a HuggingFace ``Trainer`` would restore from these object names, if anything.

    ``names`` are the keys under one ``checkpoint-{N}`` directory, relative to it.

    Three answers rather than two, because this library's own default moved. Weights plus
    ``trainer_state.json`` plus ``optimizer.pt`` is a full continuation. Weights plus
    ``trainer_state.json`` restores the step counter and the data position and starts the
    optimizer cold, and that is the ordinary shape as of transformers 5, which stops writing
    optimizer state unless asked. Weights alone is a cold start with warm parameters.

    None when there are no weights at all, which is a directory that is not a checkpoint --
    a bare ``trainer_state.json`` from a torn write, or something else entirely.

    THIS ANSWERS A DIFFERENT QUESTION FROM :func:`olmo_core_checkpoint_shape` AND THE TWO
    MUST NOT BE MERGED. That one asks whether ``Trainer.fit()`` will load a directory, and
    this asks whether ``Trainer.train(resume_from_checkpoint=...)`` will. Neither loader
    reads the other's layout, so a prefix that satisfies this one is still not resumable by
    OLMo-core, which is why the caller reports FOREIGN rather than COMMITTED.
    """
    present = set(names)
    if not any(weights in present for weights in HUGGINGFACE_WEIGHTS):
        return None
    if HUGGINGFACE_TRAINER_STATE not in present:
        return "model weights, with no trainer state, so a resume would start from step zero"
    if HUGGINGFACE_OPTIMIZER in present:
        return "model, optimizer and trainer state"
    return (
        "model and trainer state, but no optimizer state, which is what transformers 5 "
        "writes unless asked otherwise"
    )


def _listing(store: CheckpointStore, *, bucket: str, key: str) -> list[Mapping[str, Any]]:
    """Every object under this prefix, following the continuation the store hands back.

    ONE LIST CALL ANSWERS A THOUSAND KEYS AND SAYS SO IN ``IsTruncated``, AND IGNORING
    THAT DOES NOT MERELY HIDE THE OLDEST CHECKPOINTS. S3 orders keys lexicographically, so
    ``step1000/`` sorts before ``step200/`` and the thousandth key lands in the middle of
    the step sequence rather than after it. A truncated listing therefore hides an
    arbitrary subset, and which step this module reports a resume would load becomes a
    function of how many objects the run happened to write. Thirteen objects per
    checkpoint puts the boundary at seventy-six of them, which the twenty-four-hour bound on
    ``olmo-core-train`` reaches at a nineteen-minute save interval.
    """
    contents: list[Mapping[str, Any]] = []
    arguments: dict[str, Any] = {"Bucket": bucket, "Prefix": key}
    while True:
        answer = store.list_objects_v2(**arguments)
        contents.extend(
            entry for entry in (answer.get("Contents") or []) if isinstance(entry, Mapping)
        )
        token = answer.get("NextContinuationToken")
        if not answer.get("IsTruncated") or not isinstance(token, str) or not token:
            return contents
        arguments["ContinuationToken"] = token


def _uncertified_payloads(store: CheckpointStore, *, bucket: str, key: str) -> tuple[str, ...]:
    """Every object at this prefix that is not the marker, in the order the store listed them."""
    names = [
        str(entry["Key"]).removeprefix(key)
        for entry in _listing(store, bucket=bucket, key=key)
        if str(entry.get("Key", "")).startswith(key)
    ]
    return tuple(name for name in names if name and name != MARKER_OBJECT)


def _sole_payload(store: CheckpointStore, *, bucket: str, key: str) -> str | None:
    """The one object at this prefix that is not the marker, when the marker does not say.

    A compatibility path with exactly one caller in history: the first GPU training run
    wrote a hand-rolled marker carrying step, digest and size but not the name of what it
    certified. Rather than teach the reader a second format, it resolves the omission the
    only way that is unambiguous -- and refuses when it is not, because a prefix holding
    two candidate payloads and a marker that names neither is a checkpoint nobody can say
    the shape of.
    """
    candidates = _uncertified_payloads(store, bucket=bucket, key=key)
    if len(candidates) != 1:
        return None
    return candidates[0]


#: How many payload names an UNCOMMITTED detail lists before it summarises the rest. Enough
#: to recognise the write from a log line, short of pasting a directory into a Slack message.
_NAMED_PAYLOADS: Final = 3


def _uncommitted_detail(payloads: Sequence[str]) -> str:
    if len(payloads) == 1:
        return (
            f"{payloads[0]} is present and no {MARKER_OBJECT} certifies it, so the write "
            "that produced it did not finish"
        )
    shown = ", ".join(sorted(payloads)[:_NAMED_PAYLOADS])
    if len(payloads) > _NAMED_PAYLOADS:
        shown += f" and {len(payloads) - _NAMED_PAYLOADS} more"
    return (
        f"{len(payloads)} objects are present ({shown}) in neither loader's layout, and no "
        f"{MARKER_OBJECT} certifies any of them, so nothing here says which is a checkpoint "
        "or that the write that produced it finished"
    )


def _olmo_core_checkpoint(
    store: CheckpointStore,
    *,
    bucket: str,
    key: str,
) -> CheckpointInspection | None:
    """The newest ``step{N}`` directory a resume would load, judged by the library's rules.

    None when there is no step directory at all, which is how the caller tells "this is not
    a library-written checkpoint" from "it is one and it is unfinished". Those two need
    different answers: the first falls through to the marker protocol's own reading of the
    prefix, and the second is a run that died mid-write and must not be resumed from.

    THE NEWEST ACCEPTABLE DIRECTORY, WHICH IS NOT THE SAME AS THE NEWEST ONE. This used to
    read the highest step and report the prefix unusable if that one was torn, on the
    stated ground that the newest is what a resume loads and the rest are history. The
    first half of that is wrong, and it is wrong in exactly the case the whole module
    exists for. ``Checkpointer.find_checkpoints`` skips any directory failing
    ``dir_is_checkpoint`` and ``latest_checkpoint`` takes the highest of what survives, so
    an attempt reclaimed part-way through writing step 400 resumes from step 200 and keeps
    going. Reading only step 400 answered UNCOMMITTED for that run -- telling an operator
    there is nothing to resume from while the trainer beside them resumes from it, which
    is the two disagreeing about the one question this is asked.

    A prefix whose every step directory is torn is still UNCOMMITTED, and it is named by
    the newest of them, because that is the write that did not finish.
    """
    prefix_uri = f"s3://{bucket}/{key}"

    under: dict[int, dict[str, int]] = {}
    for entry in _listing(store, bucket=bucket, key=key):
        relative = str(entry.get("Key", "")).removeprefix(key)
        directory, separator, remainder = relative.partition("/")
        if not separator or not remainder:
            continue
        matched = OLMO_CORE_STEP_DIRECTORY.match(directory)
        if matched is None:
            continue
        size = entry.get("Size")
        under.setdefault(int(matched.group(1)), {})[remainder] = (
            size if isinstance(size, int) else 0
        )

    if not under:
        return None

    newest = max(under)
    # Descending, and stopping at the first the loader would take, because that is the
    # order find_checkpoints leaves latest_checkpoint to pick from.
    for step in sorted(under, reverse=True):
        shape = olmo_core_checkpoint_shape(under[step])
        if shape is not None:
            break
    else:
        return CheckpointInspection(
            prefix=prefix_uri,
            state=CheckpointState.UNCOMMITTED,
            detail=(
                f"step{newest} holds {len(under[newest])} object(s) and is not a shape "
                "OLMo-core's own loader accepts, so the write that produced it did not "
                "finish, and no earlier step directory here is one either"
            ),
        )

    members = under[step]
    detail = f"step{step} is a checkpoint OLMo-core's own loader accepts, carrying {shape}"
    if step != newest:
        detail += (
            f", and step{newest} is newer but unfinished, so a resume skips it the way "
            "the library's own loader does"
        )

    return CheckpointInspection(
        prefix=prefix_uri,
        state=CheckpointState.COMMITTED,
        detail=detail,
        manifest=CheckpointManifest(
            schema_version=1,
            uri=f"{prefix_uri}step{step}/",
            step=step,
            epoch=None,
            # The store's own timestamps, because a library-written directory carries no
            # marker recording when its writer thought it finished. The newest object in it
            # is when the directory became complete, which is the more defensible answer.
            created_at=_newest_write(store, bucket=bucket, key=f"{key}step{step}/", members=members),
            size_bytes=sum(members.values()),
            # No marker means no payload digest to record, so this is a SHA-256 over what
            # was found -- which is what described_checksum is for, and it changes when any
            # object under the directory changes.
            checksum=described_checksum(
                [(name, size, "listing") for name, size in members.items()]
            ),
            # There is none, and saying so is the honest answer rather than pointing at a
            # marker this platform did not write and the library will never read.
            success_marker_uri=None,
        ),
    )


def _huggingface_checkpoint(
    store: CheckpointStore,
    *,
    bucket: str,
    key: str,
) -> CheckpointInspection | None:
    """The newest ``checkpoint-{N}`` directory here, or weights the final save left at the root.

    None when neither is present, so the caller falls through to the marker protocol's own
    reading exactly as it does for a prefix with no ``step{N}`` directory.

    NO MANIFEST IS EVER RETURNED FROM HERE, AND THAT IS THE POINT RATHER THAN AN OMISSION.
    ``CheckpointInspection.manifest`` is populated for COMMITTED and nothing else, and
    COMMITTED means this platform's retry path may resume from it. It may not: nothing here
    passes ``resume_from_checkpoint`` to a HuggingFace ``Trainer``, and the retry rule fires
    on host loss with the same command, which starts over. Saying FOREIGN and describing
    what is there is the whole of what this can honestly claim.

    THE SECOND SHAPE IS THE FINAL SAVE AND IT SITS OUTSIDE ANY DIRECTORY. ``save_model`` at
    the end of training writes the weights straight to ``output_dir``, so a completed run
    has a bare ``adapter_model.safetensors`` at the prefix root alongside its
    ``checkpoint-{N}`` directories. Read on its own that root was falling through to
    ``_sole_payload``, which returns None for more than one non-marker object, and the
    prefix came back ABSENT. A finished run is the case most worth not calling empty.
    """
    prefix_uri = f"s3://{bucket}/{key}"

    under: dict[int, dict[str, int]] = {}
    at_root: dict[str, int] = {}
    for entry in _listing(store, bucket=bucket, key=key):
        relative = str(entry.get("Key", "")).removeprefix(key)
        size = entry.get("Size")
        size = size if isinstance(size, int) and not isinstance(size, bool) else 0
        directory, separator, remainder = relative.partition("/")
        if not separator or not remainder:
            if relative:
                at_root[relative] = size
            continue
        matched = HUGGINGFACE_CHECKPOINT_DIRECTORY.match(directory)
        if matched is None:
            continue
        under.setdefault(int(matched.group(1)), {})[remainder] = size

    # Descending, and stopping at the first one that holds weights, which mirrors what the
    # library's own `get_last_checkpoint` does after sorting on the trailing number.
    for step in sorted(under, reverse=True):
        shape = huggingface_checkpoint_shape(under[step])
        if shape is None:
            continue
        newest = max(under)
        detail = (
            f"checkpoint-{step} is a HuggingFace Trainer checkpoint carrying {shape}; "
            "OLMo-core's loader does not read this layout, so this platform's own retry "
            "would start over"
        )
        if step != newest:
            detail += f", and checkpoint-{newest} is newer and holds no weights"
        return CheckpointInspection(prefix=prefix_uri, state=CheckpointState.FOREIGN, detail=detail)

    root_shape = huggingface_checkpoint_shape(at_root)
    if root_shape is not None:
        return CheckpointInspection(
            prefix=prefix_uri,
            state=CheckpointState.FOREIGN,
            detail=(
                f"the prefix root holds {root_shape}, which is what a HuggingFace "
                "Trainer's final save_model writes outside any checkpoint directory; "
                "OLMo-core's loader does not read this layout"
            ),
        )
    return None


def _newest_write(
    store: CheckpointStore,
    *,
    bucket: str,
    key: str,
    members: Mapping[str, int],
) -> datetime:
    newest: datetime | None = None
    for name in members:
        try:
            head = store.head_object(Bucket=bucket, Key=key + name)
        except Exception as error:
            if _is_missing(error):
                continue
            raise
        written = head.get("LastModified")
        if isinstance(written, datetime) and (newest is None or written > newest):
            newest = written
    if newest is None:
        raise UnreadableCheckpointError(
            f"the store reports no LastModified for anything under s3://{bucket}/{key}, so "
            "there is no time at which this checkpoint can be said to have been written"
        )
    return newest


def inspect_checkpoint(store: CheckpointStore, *, prefix: str) -> CheckpointInspection:
    """What is at this prefix, and a manifest only if it is safe to resume from.

    The order of the questions matters. The marker is read first because it names the
    payload; only then is the payload headed, so a prefix with no marker costs one call and
    never reaches the point where it could be described as a checkpoint.

    OLMo-core IS ASKED BEFORE HUGGINGFACE AND THE ORDER IS NOT ARBITRARY. Only OLMo-core can
    return COMMITTED and a manifest, so asking it first means a prefix this platform can
    genuinely resume from is never described as something weaker. The two layouts do not
    overlap -- ``step32`` and ``checkpoint-32`` cannot both match one directory name -- so a
    prefix holding both is one this platform resumes and also reports on, rather than an
    ambiguity to break.
    """
    bucket, key = _split(prefix)
    marker = _read_marker(store, bucket=bucket, key=key)

    if marker is None:
        # A library-written checkpoint before a hand-written one, because the library is
        # what a real training run uses and it writes no marker of ours. Only reached when
        # there is no marker, so nothing this module wrote can be shadowed by it.
        library = _olmo_core_checkpoint(store, bucket=bucket, key=key)
        if library is not None:
            return library
        # Then the other trainer this platform runs. Before _sole_payload, which answers
        # only for a prefix holding exactly one non-marker object and returns None for the
        # sixteen a HuggingFace checkpoint writes -- so without this a complete checkpoint
        # fell through to ABSENT, "nothing is stored at this prefix".
        foreign = _huggingface_checkpoint(store, bucket=bucket, key=key)
        if foreign is not None:
            return foreign
        # Counted rather than resolved to a single name. _sole_payload answers only for a
        # prefix holding exactly one object, and reading its None as "empty" is the same
        # false accusation the two branches above exist to prevent: run_019fcaae left four
        # uncertified tars here and was reported as having stored nothing at all.
        payloads = _uncertified_payloads(store, bucket=bucket, key=key)
        if not payloads:
            return CheckpointInspection(
                prefix=prefix,
                state=CheckpointState.ABSENT,
                detail="nothing is stored at this prefix",
            )
        return CheckpointInspection(
            prefix=prefix,
            state=CheckpointState.UNCOMMITTED,
            detail=_uncommitted_detail(payloads),
        )

    payload_name = marker.get("payload")
    if not isinstance(payload_name, str) or not payload_name:
        payload_name = _sole_payload(store, bucket=bucket, key=key)
    if payload_name is None:
        return CheckpointInspection(
            prefix=prefix,
            state=CheckpointState.CORRUPT,
            detail=(
                f"the {MARKER_OBJECT} names no payload and this prefix does not hold exactly "
                "one object it could mean"
            ),
        )

    try:
        head = store.head_object(Bucket=bucket, Key=key + payload_name, ChecksumMode="ENABLED")
    except Exception as error:
        if _is_missing(error):
            return CheckpointInspection(
                prefix=prefix,
                state=CheckpointState.ORPHANED,
                detail=(
                    f"the {MARKER_OBJECT} certifies {payload_name}, which is not at this prefix"
                ),
            )
        raise

    # The store is asked first, because it decides which algorithm the comparison is in.
    # Reading the marker's sha256 first and then meeting a CRC32C attestation would leave two
    # digests that cannot disagree because they do not describe the same function.
    attested = _attested_digest(head)
    if attested is None:
        # NOT TREATED AS AGREEMENT, which is the tempting shortcut and the wrong one. An
        # object written without ChecksumAlgorithm, or assembled from parts under an
        # algorithm S3 can only combine compositely, gives the store nothing to say about
        # its content -- so the marker's claim is unverifiable, and an unverifiable claim
        # accepted is a claim nobody is checking.
        return CheckpointInspection(
            prefix=prefix,
            state=CheckpointState.CORRUPT,
            detail=(
                f"{payload_name} carries no whole-object checksum from the store, so the "
                f"{MARKER_OBJECT}'s claim about it cannot be verified"
            ),
        )
    algorithm = attested.split(":", 1)[0]
    claimed = _normalise_digest(marker.get(algorithm), algorithm=algorithm)
    if claimed is None:
        return CheckpointInspection(
            prefix=prefix,
            state=CheckpointState.CORRUPT,
            detail=(
                f"the store attests a {algorithm} digest for {payload_name} and the "
                f"{MARKER_OBJECT} carries no readable {algorithm} to compare it against"
            ),
        )
    if attested != claimed:
        return CheckpointInspection(
            prefix=prefix,
            state=CheckpointState.CORRUPT,
            detail=(
                f"the {MARKER_OBJECT} certifies {claimed} and the store attests {attested} "
                f"for {payload_name}, so they describe different bytes"
            ),
        )

    size = head.get("ContentLength")
    if not isinstance(size, int) or size <= 0:
        return CheckpointInspection(
            prefix=prefix,
            state=CheckpointState.CORRUPT,
            detail=f"the store reports no usable length for {payload_name}",
        )
    recorded = marker.get("bytes")
    if isinstance(recorded, int) and recorded != size:
        return CheckpointInspection(
            prefix=prefix,
            state=CheckpointState.CORRUPT,
            detail=(
                f"the {MARKER_OBJECT} certifies {recorded} bytes and the store holds {size}"
            ),
        )

    step = marker.get("step")
    if not isinstance(step, int) or isinstance(step, bool) or step < 0:
        return CheckpointInspection(
            prefix=prefix,
            state=CheckpointState.CORRUPT,
            detail=f"the {MARKER_OBJECT} carries no readable step",
        )
    epoch = marker.get("epoch")
    written_at = marker.get("created_at")
    if isinstance(written_at, str):
        try:
            created_at = datetime.fromisoformat(written_at)
        except ValueError as error:
            raise UnreadableCheckpointError(
                f"the {MARKER_OBJECT} at {prefix} carries an unparseable created_at"
            ) from error
    else:
        # The store's own LastModified, for a marker that did not record one. It is when
        # the payload landed rather than when the writer thought it did, which is the more
        # defensible of the two anyway and is the only one available here.
        last_modified = head.get("LastModified")
        if not isinstance(last_modified, datetime):
            raise UnreadableCheckpointError(
                f"the {MARKER_OBJECT} at {prefix} records no created_at and the store "
                "reports no LastModified for the payload"
            )
        created_at = last_modified

    return CheckpointInspection(
        prefix=prefix,
        state=CheckpointState.COMMITTED,
        detail=f"{payload_name} is certified by its {MARKER_OBJECT} and the store agrees",
        manifest=CheckpointManifest(
            schema_version=1,
            uri=prefix,
            step=step,
            epoch=epoch if isinstance(epoch, int) and not isinstance(epoch, bool) else None,
            created_at=created_at,
            size_bytes=size,
            # The marker's own SHA-256 when it has one, because that is the digest of the
            # payload itself and is strictly more informative. Otherwise a SHA-256 over what
            # was verified, because the field is typed to SHA-256 and the store attested a
            # CRC32C.
            checksum=(
                _normalise_digest(marker.get("sha256"))
                or described_checksum([(payload_name, size, attested)])
            ),
            success_marker_uri=prefix + MARKER_OBJECT,
        ),
    )


def resumable_checkpoint(store: CheckpointStore, *, prefix: str) -> CheckpointManifest | None:
    """The manifest to resume from, or None because there is nothing safe to resume from.

    What a training loop calls. It returns None for every unusable state rather than
    raising, because "no usable checkpoint" is the ordinary condition at the start of every
    first run, and a resume path that had to catch an exception to start from scratch would
    end up catching the ones that mean something else too.
    """
    return inspect_checkpoint(store, prefix=prefix).manifest
