"""An object store that attests its own digest, the way S3 does when asked to.

Shared by the tests for the checkpoint protocol and for the submission that uses it, so
both exercise the same store rather than two fakes that happen to behave alike.

**The attestation is the point.** A fake that echoed back whatever digest the writer claimed
could never express a marker and a payload that disagree -- which is precisely the state a
retry after a half-finished commit produces, and the state the reader exists to catch. Here
the digest is computed over the bytes the store received, so writing a payload and then
replacing it leaves the marker certifying something that is no longer there.

``attest`` follows ``ChecksumAlgorithm`` rather than being always on, so an object written
without it comes back carrying no ``ChecksumSHA256`` at all. That is the third state the
reader has to distinguish -- not agreement, not disagreement, but nothing to compare -- and
it is unreachable in a fake that always attests.
"""

from __future__ import annotations

import base64
import hashlib
import io
from datetime import UTC, datetime
from typing import Any

from edullm_platform.checkpoints import crc32c

#: When the fake says an object landed. A fixed instant, because a store with a clock in it
#: makes every test that reads a timestamp depend on when it ran.
STORED_AT = datetime(2026, 7, 29, 2, 14, 44, tzinfo=UTC)

FULL_OBJECT = "FULL_OBJECT"
COMPOSITE = "COMPOSITE"


def missing(code: str = "NoSuchKey") -> Exception:
    """S3's answer for a key that is not there, in the shape botocore raises it."""
    error = RuntimeError(code)
    error.response = {  # type: ignore[attr-defined]
        "Error": {"Code": code},
        "ResponseMetadata": {"HTTPStatusCode": 404},
    }
    return error


def throttled() -> Exception:
    """A failure that is emphatically not an absence, for the reader that must tell them apart."""
    error = RuntimeError("SlowDown")
    error.response = {  # type: ignore[attr-defined]
        "Error": {"Code": "SlowDown"},
        "ResponseMetadata": {"HTTPStatusCode": 503},
    }
    return error


class FakeObjectStore:
    def __init__(self) -> None:
        self.objects: dict[str, dict[str, Any]] = {}
        self.writes: list[dict[str, Any]] = []
        self.refuse: dict[str, Exception] = {}

    def put(
        self,
        key: str,
        body: bytes,
        *,
        attest: bool = True,
        composite: bool = False,
        algorithm: str = "SHA256",
    ) -> None:
        """Place an object directly, for the states a well-behaved writer never produces.

        ``algorithm`` follows what S3 would have been asked for. It matters because S3
        attests the algorithm the writer named and no other, so an object written under
        CRC32C carries no ``ChecksumSHA256`` at all -- and a fake that populated both
        would let a reader pass by finding the field it happened to prefer.
        """
        stored: dict[str, Any] = {
            "Body": body,
            "ContentLength": len(body),
            "LastModified": STORED_AT,
            "ChecksumSHA256": None,
            "ChecksumCRC32C": None,
            "ChecksumType": COMPOSITE if composite else FULL_OBJECT,
        }
        if attest and algorithm == "SHA256":
            stored["ChecksumSHA256"] = base64.b64encode(hashlib.sha256(body).digest()).decode()
        if attest and algorithm == "CRC32C":
            raw = crc32c(body).to_bytes(4, "big")
            stored["ChecksumCRC32C"] = base64.b64encode(raw).decode()
        self.objects[key] = stored

    def put_object(self, **arguments: Any) -> Any:
        self.writes.append(arguments)
        algorithm = arguments.get("ChecksumAlgorithm")
        self.put(
            arguments["Key"],
            arguments["Body"],
            attest=algorithm in {"SHA256", "CRC32C"},
            algorithm=algorithm if isinstance(algorithm, str) else "SHA256",
        )
        # S3 answers a PutObject with the checksum it computed, and a writer that reads it
        # back rather than recomputing depends on that. A fake returning an empty response
        # would make such a writer look broken here and work in the account, which is the
        # wrong way round for a fake to be wrong.
        stored = self.objects[arguments["Key"]]
        return {
            field: stored[field]
            for field in ("ChecksumSHA256", "ChecksumCRC32C")
            if stored.get(field) is not None
        }

    def _stored(self, key: str) -> dict[str, Any]:
        refusal = self.refuse.get(key)
        if refusal is not None:
            raise refusal
        stored = self.objects.get(key)
        if stored is None:
            raise missing()
        return stored

    def head_object(self, **arguments: Any) -> Any:
        stored = self._stored(arguments["Key"])
        head = {
            "ContentLength": stored["ContentLength"],
            "LastModified": stored["LastModified"],
            "ChecksumType": stored["ChecksumType"],
        }
        for field in ("ChecksumSHA256", "ChecksumCRC32C"):
            if stored.get(field) is not None:
                head[field] = stored[field]
        return head

    def get_object(self, **arguments: Any) -> Any:
        return {"Body": io.BytesIO(self._stored(arguments["Key"])["Body"])}

    def list_objects_v2(self, **arguments: Any) -> Any:
        prefix = arguments["Prefix"]
        # Size is included because S3 includes it, and a reader that sums the listing to
        # size a checkpoint would otherwise see zero here and something real in the account.
        return {
            "Contents": [
                {"Key": key, "Size": self.objects[key]["ContentLength"]}
                for key in sorted(self.objects)
                if key.startswith(prefix)
            ]
        }

    @property
    def written_keys(self) -> list[str]:
        return [write["Key"] for write in self.writes]
