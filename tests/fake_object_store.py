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

    def put(self, key: str, body: bytes, *, attest: bool = True, composite: bool = False) -> None:
        """Place an object directly, for the states a well-behaved writer never produces."""
        digest = hashlib.sha256(body).digest()
        self.objects[key] = {
            "Body": body,
            "ContentLength": len(body),
            "LastModified": STORED_AT,
            "ChecksumSHA256": base64.b64encode(digest).decode() if attest else None,
            "ChecksumType": COMPOSITE if composite else FULL_OBJECT,
        }

    def put_object(self, **arguments: Any) -> Any:
        self.writes.append(arguments)
        self.put(
            arguments["Key"],
            arguments["Body"],
            attest=arguments.get("ChecksumAlgorithm") == "SHA256",
        )
        return {}

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
        if stored["ChecksumSHA256"] is not None:
            head["ChecksumSHA256"] = stored["ChecksumSHA256"]
        return head

    def get_object(self, **arguments: Any) -> Any:
        return {"Body": io.BytesIO(self._stored(arguments["Key"])["Body"])}

    def list_objects_v2(self, **arguments: Any) -> Any:
        prefix = arguments["Prefix"]
        return {"Contents": [{"Key": key} for key in sorted(self.objects) if key.startswith(prefix)]}

    @property
    def written_keys(self) -> list[str]:
        return [write["Key"] for write in self.writes]
