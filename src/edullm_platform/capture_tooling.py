"""What every capture tool does the same way, extracted once rather than a fifth time.

Four capture tools exist -- Phase 0 at 456 lines, Phase 1 at 1,186, Phase 2 at 409 and
Phase 3 at 1,729 -- and they differ in what they gather while being near-identical in how
they gather it. Each shells out to a CLI and parses JSON, each refuses to write outside the
working directory, each serializes a contract model the same way, each scans what it wrote
for a credential, and each ends in a ``main`` whose four arms repeat the same
``except CaptureFailedError / except OSError`` pair.

**The standing rule is to consolidate before a fifth copy is written, and Phase 4 is that
fifth.** This module is the part of the answer that Phase 4 actually needs: the mechanics,
not a framework. What a phase supplies is still what it should supply -- which facts to
gather, and where they land.

**What is deliberately not here.** No registry of phases, no base class a tool inherits
from, no declarative description of a capture. Each of the four tools does something
genuinely different in the middle, and a design that pretended otherwise would push the
differences into configuration that reads worse than the code it replaced. The line is:
anything that talks to a CLI, a filesystem or an exit code belongs here; anything that
knows what a Phase 3 role or a Phase 4 GPU job *is* does not.

**There is no GitHub CLI wrapper here, and that is measured rather than an oversight.**
One tool shells out to ``gh``, and what its failures print is the service's own stderr --
which is the whole value of the message to an operator whose ``gh api`` call was refused,
and precisely what a machine-readable reason token throws away. A wrapper that served it
would have to be a different function from the one that serves ``aws``.

**Why the write path scans.** A capture reads a live account, and the difference between a
record and a leak is one field. ``scan_for_secrets`` refuses a serialization that looks like
it carries a credential, which is a shape test and therefore both imperfect and cheap: it
cannot recognise every secret, and it costs nothing to run on every write. The cases where
it is *wrong* -- a manifest digest that looks like a key -- are handled where they arise,
by the caller that knows the field is a digest, rather than by weakening the scan.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from edullm_platform.evidence import (
    AWS_ACCOUNT_ID_PATTERN,
    redact_aws_account_ids,
    redact_content_digests,
    scan_for_secrets,
)

__all__ = [
    "AWS_CALL_TIMEOUT_SECONDS",
    "EXIT_OK",
    "EXIT_UNUSABLE",
    "AccountIdentity",
    "CaptureFailedError",
    "account_identity",
    "aws",
    "aws_json",
    "check_output_location",
    "observed_now",
    "run_capture",
    "write_model",
    "write_record",
    "write_sanitized_text",
]

#: Long enough for a CloudTrail lookup over a wide window, short enough that a hung call
#: fails the capture rather than holding a terminal open until somebody notices.
AWS_CALL_TIMEOUT_SECONDS: Final = 90

EXIT_OK: Final = 0
EXIT_UNUSABLE: Final = 2


class CaptureFailedError(RuntimeError):
    """The capture could not be taken, or would have written somewhere it must not.

    ``reason`` is a colon-delimited machine-readable token rather than a sentence, because
    the four tools print it to stderr and an operator greps it. The sentence, where one
    helps, goes in the docstring of whatever raised.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def observed_now() -> datetime:
    """When this capture was taken, to the second.

    Truncated because the sub-second component is noise that makes two records taken in one
    command look like two observations, and because every reader of these timestamps is
    asking a question about minutes.
    """
    return datetime.now(tz=UTC).replace(microsecond=0)


def aws(
    arguments: Sequence[str], *, profile: str, region: str | None = None
) -> subprocess.CompletedProcess[str]:
    """One AWS CLI call, returned whole so a caller can read a non-zero exit deliberately.

    The CLI rather than boto3, and not by accident. A ``--dry-run`` probe is only
    interpretable through the error code it prints, the tools inherit whatever session the
    operator already has so there is no credential for them to store, and boto3 is not a
    project dependency -- adding it would put the whole SDK into the admission validator's
    zip.

    ``shell=False`` and a list, so an argument containing a space is an argument rather
    than two.
    """
    command = ["aws", *arguments, "--profile", profile, "--output", "json"]
    if region is not None:
        command += ["--region", region]
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=AWS_CALL_TIMEOUT_SECONDS,
            shell=False,
        )
    except subprocess.TimeoutExpired as error:
        raise CaptureFailedError(f"aws_call_timed_out:{':'.join(arguments[:2])}") from error
    except OSError as error:
        raise CaptureFailedError("aws_cli_unavailable") from error


def aws_json(arguments: Sequence[str], *, profile: str, region: str | None = None) -> Any:
    """One AWS CLI call whose answer is required, parsed.

    A non-zero exit is a capture failure and never an empty record. That distinction is the
    whole point: an empty record and a failed call look identical in a committed fixture,
    and the empty one reads as a true statement that there was nothing there.
    """
    completed = aws(arguments, profile=profile, region=region)
    if completed.returncode != 0:
        raise CaptureFailedError(f"aws_call_failed:{':'.join(arguments[:2])}")
    if not completed.stdout.strip():
        return {}
    try:
        return json.loads(completed.stdout)
    except ValueError as error:
        raise CaptureFailedError(f"aws_answer_unreadable:{arguments[0]}") from error


@dataclass(frozen=True)
class AccountIdentity:
    """Who a capture is running as. Neither field is ever written to a file.

    The ARN is carried beside the account id rather than parsed here, because what a
    caller wants out of it is the partition, and which partition spellings may be folded
    together is a question the role-drift comparison owns rather than this module.
    """

    account_id: str
    arn: str


def account_identity(*, profile: str, region: str) -> AccountIdentity:
    """The account this is running against. Never written to a file.

    Returned because a captured ARN naming *this* account has to be distinguishable from
    one naming another before the id is masked out of both. Every tool needs it and none of
    them should record it.
    """
    identity = aws_json(["sts", "get-caller-identity"], profile=profile, region=region)
    account_id = identity.get("Account")
    arn = identity.get("Arn")
    if not isinstance(account_id, str) or not account_id or not isinstance(arn, str):
        raise CaptureFailedError("caller_identity_unreadable")
    return AccountIdentity(account_id=account_id, arn=arn)


def check_output_location(path: Path, *, allowed_suffix: Path) -> None:
    """Refuse to write anywhere but the phase's working directory.

    A capture is local until somebody has read it and copied what they want into
    ``fixtures/``, and that copy is a review step rather than a formality -- this reads a
    live account, and the difference between what it found and what the repository already
    claims is precisely what a reader is there to notice. Writing straight into
    ``fixtures/`` would skip the only moment anybody looks.
    """
    if allowed_suffix.as_posix() not in path.resolve().as_posix():
        raise CaptureFailedError(f"output_must_be_under:{allowed_suffix.as_posix()}")


def write_record(
    path: Path, record: Mapping[str, Any], *, allow_content_digests: bool = False
) -> None:
    """One record as committed: indented, key-sorted, and scanned before it lands.

    ``allow_content_digests`` masks a ``sha256:`` digest and a bare commit SHA before the
    scan, and defaults to off. It exists because a Phase 4 record legitimately carries the
    commit the run was submitted at, and forty hexadecimal characters is also the shape of
    an AWS secret access key -- so the strict scan refuses the evidence for being valid.

    Off by default rather than on, because this scan is a *backstop*. Each field has already
    been read through its own annotation, which is where a model states that a particular
    field may hold a digest. Turning the masking on here says the same thing about the whole
    document, which is a weaker statement, so it is the caller's to make deliberately for a
    record whose shape it knows.

    What the masking gives up, exactly: the ability to notice a real forty-character key in
    a document that also holds commit SHAs. Everything else -- an account id, a GitHub
    token, a JWT, a PEM block, a session token -- is still refused either way.
    """
    serialized = json.dumps(record, indent=2, sort_keys=True) + "\n"
    scanned = redact_content_digests(serialized) if allow_content_digests else serialized
    try:
        scan_for_secrets(scanned)
    except ValueError as error:
        raise CaptureFailedError("record_holds_a_credential") from error
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialized, encoding="utf-8")


def write_model(path: Path, record: Any, *, allow_content_digests: bool = False) -> None:
    """One contract model, in the serialization every phase's captures already use.

    ``exclude_none=False`` on purpose. A field that is absent and a field that is null mean
    different things in a record about an account -- "not applicable here" against "we
    looked and there was nothing" -- and dropping the nulls erases the second.
    """
    write_record(
        path,
        record.model_dump(mode="json", by_alias=True, exclude_none=False),
        allow_content_digests=allow_content_digests,
    )


def write_sanitized_text(path: Path, text: str) -> None:
    """Text captured verbatim from a store, with the account id masked and then checked.

    Not put through ``scan_for_secrets``. A record captured verbatim legitimately carries a
    manifest digest, and the shape-based scan cannot tell sixty-four hexadecimal characters
    from a credential, so scanning here would refuse evidence for being what it is. What is
    checked instead is the thing that actually must not be committed, by the pattern that
    defines it.

    **The check removes digests before it looks, and must.** ``redact_aws_account_ids``
    deliberately leaves a ``sha256:``-prefixed digest and a commit sha intact -- masking
    them would destroy the identifiers the record exists to carry -- and roughly one digest
    in six contains twelve consecutive decimal characters. Searching the masked text
    directly reports such a digest as a surviving account id, which is a capture that fails
    on evidence for being valid. Three Phase 3 runs were captured before one turned up whose
    digest happened to trip it.
    """
    masked = redact_aws_account_ids(text)
    if AWS_ACCOUNT_ID_PATTERN.search(redact_content_digests(masked)):
        raise CaptureFailedError(f"account_id_survived_redaction:{path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(masked, encoding="utf-8")


def run_capture(target: Callable[[], int], *, output_dir: Path, allowed_suffix: Path) -> int:
    """Check where the output is going, run one target, and map its failures to exit codes.

    The four tools each repeat this block once per target -- Phase 3 four times in one
    ``main`` -- and every copy is the same three lines with the same two ``except`` arms.
    Extracted so that the location check cannot be forgotten on the fifth arm somebody adds,
    which is the failure this shape actually has: an arm that skips it writes a live capture
    into ``fixtures/`` and nothing says so.

    A target's own return value is passed through, because "captured, and found drift" is a
    real answer that is neither success nor an unusable capture.
    """
    try:
        check_output_location(output_dir, allowed_suffix=allowed_suffix)
        return target()
    except CaptureFailedError as error:
        print(error.reason, file=sys.stderr)
        return EXIT_UNUSABLE
    except OSError:
        print("output_unwritable", file=sys.stderr)
        return EXIT_UNUSABLE


def report(summary: Mapping[str, Any]) -> None:
    """What a capture printed, in the shape every tool already prints it."""
    print(json.dumps(summary, indent=2, sort_keys=True))
