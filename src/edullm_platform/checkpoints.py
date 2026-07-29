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
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Final, Protocol
from urllib.parse import urlparse

from edullm_platform.contracts.results import CheckpointManifest

__all__ = [
    "CHECKSUM_ALGORITHM",
    "FULL_OBJECT_CHECKSUM",
    "MARKER_OBJECT",
    "MARKER_SCHEMA_VERSION",
    "MISSING_OBJECT_CODES",
    "CheckpointInspection",
    "CheckpointState",
    "CheckpointStore",
    "UnreadableCheckpointError",
    "commit_checkpoint",
    "inspect_checkpoint",
    "resumable_checkpoint",
    "success_marker_bytes",
]

#: The one object whose presence means "the payload beside me is whole". Spelled here
#: because a reader and a writer that each chose their own name would agree until one of
#: them was edited, and the failure would be a checkpoint that silently stopped resuming.
MARKER_OBJECT: Final = "_SUCCESS"

MARKER_SCHEMA_VERSION: Final = 1

#: Asks S3 to compute and store a SHA-256 over the bytes it received. The same field the
#: lineage handler sets, for the same reason and with the same history: omitting it costs
#: nothing at write time, is invisible in every response, and produces an object that reads
#: exactly like an attested one until a reader asks for the digest and finds no field. Here
#: it is load-bearing rather than merely good practice -- without the store's own digest
#: there is nothing to compare the marker's claim against, and the reader degrades to
#: trusting whatever the marker says.
CHECKSUM_ALGORITHM: Final = "SHA256"

#: What S3 calls a digest over the whole object, as against ``COMPOSITE`` -- the digest of
#: concatenated part digests that a multipart upload produces. A composite value is not the
#: SHA-256 of the object and comparing one against the marker's claim would fail every
#: time, so it is refused with its own reason rather than reported as corruption.
FULL_OBJECT_CHECKSUM: Final = "FULL_OBJECT"

#: What S3 answers when the key is not there. Both spellings, plus the bare status: the
#: REST error for GetObject is ``NoSuchKey`` and for HeadObject it is ``404``/``NotFound``,
#: and recognising one and not the others would report an absent marker as an outage.
MISSING_OBJECT_CODES: Final = frozenset({"NoSuchKey", "NotFound", "404"})


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
    """

    ABSENT = "absent"
    UNCOMMITTED = "uncommitted"
    ORPHANED = "orphaned"
    CORRUPT = "corrupt"
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


def _attested_digest(head: Mapping[str, Any]) -> str | None:
    """The digest S3 says it computed over the bytes it received, as ``sha256:<hex>``.

    None rather than a guess when the object carries no whole-object checksum. That is
    either an object written without ``ChecksumAlgorithm`` or one assembled from parts, and
    in both cases there is no attestation to compare the marker against -- which the caller
    has to be able to distinguish from an attestation that disagrees.
    """
    encoded = head.get("ChecksumSHA256")
    if not isinstance(encoded, str) or not encoded:
        return None
    if head.get("ChecksumType") not in (None, FULL_OBJECT_CHECKSUM):
        return None
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(raw) != hashlib.sha256().digest_size:
        return None
    return f"sha256:{raw.hex()}"


def _normalise_digest(claimed: object) -> str | None:
    """The marker's claim as ``sha256:<hex>``, accepting the bare hex it may carry.

    The bare form is accepted because the first GPU training run wrote one, months before
    this module existed, and refusing it would mean this reader cannot read the only real
    checkpoint the platform has produced. Everything :func:`commit_checkpoint` writes
    carries the prefix, so the tolerance is a compatibility path and not a second format.
    """
    if not isinstance(claimed, str):
        return None
    text = claimed.removeprefix("sha256:").strip().lower()
    if len(text) != hashlib.sha256().digest_size * 2:
        return None
    try:
        bytes.fromhex(text)
    except ValueError:
        return None
    return f"sha256:{text}"


def success_marker_bytes(
    *,
    step: int,
    payload_name: str,
    digest: str,
    size_bytes: int,
    created_at: datetime,
    epoch: int | None = None,
) -> bytes:
    """The marker's content, as the bytes that go into the object.

    Sorted keys and a compact separator so that committing the same checkpoint twice
    produces byte-identical markers, which is what lets a reader compare two attempts'
    output rather than only their digests.

    ``payload`` is named rather than implied. A marker that did not say what it certified
    would leave the reader to guess, and the guess -- "the one other object here" -- stops
    being right the moment a checkpoint is sharded across files.
    """
    marker = {
        "schema_version": MARKER_SCHEMA_VERSION,
        "step": step,
        "payload": payload_name,
        "sha256": digest,
        "bytes": size_bytes,
        "created_at": created_at.isoformat(),
    }
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


def _sole_payload(store: CheckpointStore, *, bucket: str, key: str) -> str | None:
    """The one object at this prefix that is not the marker, when the marker does not say.

    A compatibility path with exactly one caller in history: the first GPU training run
    wrote a hand-rolled marker carrying step, digest and size but not the name of what it
    certified. Rather than teach the reader a second format, it resolves the omission the
    only way that is unambiguous -- and refuses when it is not, because a prefix holding
    two candidate payloads and a marker that names neither is a checkpoint nobody can say
    the shape of.
    """
    answer = store.list_objects_v2(Bucket=bucket, Prefix=key)
    contents = answer.get("Contents") or []
    names = [
        str(entry["Key"]).removeprefix(key)
        for entry in contents
        if isinstance(entry, Mapping) and str(entry.get("Key", "")).startswith(key)
    ]
    candidates = [name for name in names if name and name != MARKER_OBJECT]
    if len(candidates) != 1:
        return None
    return candidates[0]


def inspect_checkpoint(store: CheckpointStore, *, prefix: str) -> CheckpointInspection:
    """What is at this prefix, and a manifest only if it is safe to resume from.

    The order of the questions matters. The marker is read first because it names the
    payload; only then is the payload headed, so a prefix with no marker costs one call and
    never reaches the point where it could be described as a checkpoint.
    """
    bucket, key = _split(prefix)
    marker = _read_marker(store, bucket=bucket, key=key)

    if marker is None:
        payload_name = _sole_payload(store, bucket=bucket, key=key)
        if payload_name is None:
            return CheckpointInspection(
                prefix=prefix,
                state=CheckpointState.ABSENT,
                detail="nothing is stored at this prefix",
            )
        return CheckpointInspection(
            prefix=prefix,
            state=CheckpointState.UNCOMMITTED,
            detail=(
                f"{payload_name} is present and no {MARKER_OBJECT} certifies it, so the write "
                "that produced it did not finish"
            ),
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

    claimed = _normalise_digest(marker.get("sha256"))
    if claimed is None:
        return CheckpointInspection(
            prefix=prefix,
            state=CheckpointState.CORRUPT,
            detail=f"the {MARKER_OBJECT} carries no readable sha256 for {payload_name}",
        )
    attested = _attested_digest(head)
    if attested is None:
        # NOT TREATED AS AGREEMENT, which is the tempting shortcut and the wrong one. An
        # object written without ChecksumAlgorithm, or assembled from parts, gives the store
        # nothing to say about its content -- so the marker's claim is unverifiable, and an
        # unverifiable claim accepted is a claim nobody is checking.
        return CheckpointInspection(
            prefix=prefix,
            state=CheckpointState.CORRUPT,
            detail=(
                f"{payload_name} carries no whole-object SHA-256 from the store, so the "
                f"{MARKER_OBJECT}'s claim about it cannot be verified"
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
            checksum=attested,
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
