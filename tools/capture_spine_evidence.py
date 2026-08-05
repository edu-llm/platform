"""The spine's done-condition, captured so it can be re-checked without the account.

``tools/compare_two_runs.py`` answers whether two runs of one submission differ only in
fields a named cause explains, and it answers into a terminal. The spine's done-condition
is that answer, so leaving it in a terminal means the claim survives exactly as long as
somebody's scrollback. This writes it down as a record under ``fixtures/evidence/``, in the
shape every other committed capture uses, and :mod:`tests.test_spine_two_runs` is what
re-checks it afterwards.

**The comparison half is recomputed and the narrative half is pinned, which is the whole
design.** Re-running this against a synced lineage tree re-derives every difference and
every agreed field from the records themselves, so the artifact cannot drift from the store
without this saying so. What it cannot derive is what the pair does not establish, because
that is a judgement about scope rather than a field in a record. That text lives in
:data:`DOES_NOT_ESTABLISH` below, under review like any other code, and :data:`THE_PAIR`
pins it to the two ids it was written about. Point this at a different pair and it refuses
rather than attaching one pair's caveats to another pair's comparison.

**Why it reads two object heads out of S3.** The lineage record cannot say whether the two
checkpoints hold the same weights. ``result.checkpoints[].checksum`` is a SHA-256 over the
names and sizes a listing returned, so both runs record one value in it whatever the bytes
are, and the comparison therefore prints no ``checksum`` row at all. A reader takes that
silence for agreement. Two HEAD requests with ``--checksum-mode ENABLED`` get S3's own
CRC32C for each payload, which is the store speaking about the bytes rather than about the
container, and it costs two requests rather than a gigabyte of transfer.

**What is written where it is told, and why that differs from the phase captures.** Those
refuse to write outside ``docs-frank/working/`` because they gather a live account and a
person has to read the difference between what was found and what this repository already
claims before any of it is committed. This gathers one comparison of two records that are
already public in the sense that matters, has no field a role name or an ARN could reach,
and is the artifact a plan asked for by path. It still goes out through ``write_model``, so
the same scan refuses a serialization carrying anything credential-shaped.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, TypedDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from edullm_platform.capture_tooling import (
    EXIT_UNUSABLE,
    CaptureFailedError,
    aws_json,
    observed_now,
    report,
    write_model,
)
from edullm_platform.run_comparison import (
    CheckpointPayloadReading,
    ComparedField,
    RecordedRun,
    TwoRunComparison,
    TwoRunEvidence,
    agreed_required_fields,
    cause_for,
    checkpoint_coverage,
    compare_runs,
    read_run,
    required_field_coverage,
    unexplained,
)

__all__ = [
    "DOES_NOT_ESTABLISH",
    "ESTABLISHES",
    "MEASURED_BYTE_DIVERGENCE",
    "OUTPUTS_BUCKET",
    "SUCCESS_MARKER",
    "THE_PAIR",
    "AttestedPayload",
    "ByteDivergence",
    "build_evidence",
    "main",
    "payload_reading",
]

#: The bucket a run's checkpoints land in. Written here rather than parsed out of a
#: recorded URI, so a capture aimed at a bucket this project does not own fails on the
#: name instead of quietly heading objects somebody else wrote.
OUTPUTS_BUCKET: Final = "sbsandbox-intern-edullm-outputs"

#: The object beside a payload that says the write finished. Excluded from the payload
#: reading because it is 227 bytes of metadata and differs between two runs by design.
SUCCESS_MARKER: Final = "_SUCCESS"

#: The two ids :data:`DOES_NOT_ESTABLISH` was written about. Pinned so that pointing this
#: at another pair refuses rather than producing a document whose caveats describe two
#: runs it does not contain.
THE_PAIR: Final = (
    "run_019fd3a1-d2f8-70e5-b453-2743c4400c35",
    "run_019fd3a2-fa48-706d-9d77-9b2e262f7f63",
)

ESTABLISHES: Final = (
    "Two dispatches of one submission on gpu-1xt4, eighty-four seconds apart from a form "
    "that did not change between them, produced two lineage records differing only in "
    "fields a named cause explains. Both compiled to one manifest digest, both were "
    "classified automatic and released by nobody, and both finished succeeded with exit "
    "code 0. Each wrote one checkpoint at step 20 that the recorder could read, so the "
    "checkpoint fields are inside the comparison rather than absent from it."
)

#: What a reader must not take from the document above. Ordered by how badly each one
#: would mislead somebody who read only the word "reproducible".
#:
#: **The first line is the one this list exists for.** Two agreeing records are not two
#: agreeing computations, and nothing in the comparison can tell them apart, so the
#: difference has to be stated where the comparison is rather than in a plan.
DOES_NOT_ESTABLISH: Final = (
    (
        "The two runs are not the same computation, and this establishes only that their "
        "records agree. Their checkpoint payloads are the same 762,258,865 bytes long and "
        "S3 attests a different CRC32C for each. 716,708,889 of those bytes differ, which "
        "is 94 per cent of the file, and the first difference is at offset 30,080, so the "
        "two agree across the archive header and then diverge. The losses diverge from the "
        "first step. That is ordinary floating-point reduction order on a GPU and not a "
        "defect. This platform has never claimed a workload is bit-reproducible."
    ),
    (
        "Nothing about the platform comparing checkpoint payloads, because it cannot. "
        "result.checkpoints[].checksum is a SHA-256 over the names and sizes a listing "
        "returned rather than a digest of the bytes, so both runs record one value in it "
        "and no checksum row appears in the comparison. That silence is not agreement. "
        "Closing it needs a read grant the lifecycle recorder deliberately does not hold."
    ),
    (
        "Nothing about a multi-hour run. Each of these took about seven seconds of compute "
        "over twenty steps, inside a container alive for about twenty-five seconds."
    ),
    "Nothing about a fan-out. Both runs are one cell on one instance.",
    (
        "Nothing about a resume from a checkpoint. Both runs wrote one and neither read "
        "one back to continue from."
    ),
    (
        "Nothing about a real corpus. The workload draws random bytes from a uniform "
        "distribution and its dataset release is none, so no data reached it through the "
        "airlock."
    ),
    (
        "Nothing about the retry path. Each run took exactly one attempt, so no attempt "
        "after the first has ever been part of a comparison."
    ),
)


class ByteDivergence(TypedDict):
    """The part of the payload divergence a HEAD cannot produce, and what did produce it."""

    differing_bytes: int
    first_differing_offset: int
    measured_by: str


#: Both objects downloaded whole and compared byte by byte on 2026-08-05, which is a
#: gigabyte and a half of transfer and is why it is recorded rather than repeated on every
#: capture. The CRC32C beside it is re-read live each time, so a reading that stopped being
#: true of the objects in the bucket would disagree with the numbers here rather than
#: replace them silently.
MEASURED_BYTE_DIVERGENCE: Final[ByteDivergence] = {
    "differing_bytes": 716_708_889,
    "first_differing_offset": 30_080,
    "measured_by": (
        "both objects downloaded whole and compared byte by byte on 2026-08-05, against "
        "the digest each run's own _SUCCESS recorded"
    ),
}


def _payload_key(run_id: str, prefix: str, *, profile: str, region: str) -> str:
    """The one object under a checkpoint prefix that is not the success marker.

    Refuses anything but exactly one, rather than picking the largest or the first. A
    prefix holding two payloads is a checkpoint shape this reading was not written for, and
    a reading that quietly chose one of them would put a CRC32C in the record against a
    file nobody named.
    """
    listing = aws_json(
        ["s3api", "list-objects-v2", "--bucket", OUTPUTS_BUCKET, "--prefix", prefix],
        profile=profile,
        region=region,
    )
    keys = [
        str(entry["Key"])
        for entry in listing.get("Contents") or []
        if not str(entry["Key"]).endswith(f"/{SUCCESS_MARKER}")
    ]
    if len(keys) != 1:
        raise CaptureFailedError(f"checkpoint_is_not_one_payload:{run_id}:{len(keys)}")
    return keys[0]


def _prefix_of(run: RecordedRun) -> str:
    """The checkpoint prefix this run recorded, as a bucket-relative key prefix.

    Read out of the record rather than composed from the run id, because the directory
    name is chosen by the training program at dispatch time. Composing it here would make
    this agree with whatever the program did last rather than with what the run recorded.
    """
    recorded = run.field_map().get("result.checkpoints[0].uri")
    if recorded is None:
        raise CaptureFailedError(f"run_recorded_no_checkpoint:{run.run_id}")
    uri = json.loads(recorded)
    wanted = f"s3://{OUTPUTS_BUCKET}/"
    if not isinstance(uri, str) or not uri.startswith(wanted):
        raise CaptureFailedError(f"checkpoint_is_not_in_this_bucket:{run.run_id}")
    return uri[len(wanted) :]


@dataclass(frozen=True)
class AttestedPayload:
    """One payload as S3 describes it, which is the only side that can describe the bytes."""

    size_bytes: int
    crc32c: str


def _attested(run: RecordedRun, *, profile: str, region: str) -> AttestedPayload:
    key = _payload_key(run.run_id, _prefix_of(run), profile=profile, region=region)
    head = aws_json(
        [
            "s3api",
            "head-object",
            "--bucket",
            OUTPUTS_BUCKET,
            "--key",
            key,
            "--checksum-mode",
            "ENABLED",
        ],
        profile=profile,
        region=region,
    )
    checksum = head.get("ChecksumCRC32C")
    if not isinstance(checksum, str) or not checksum:
        raise CaptureFailedError(f"payload_carries_no_crc32c:{run.run_id}")
    return AttestedPayload(size_bytes=int(head["ContentLength"]), crc32c=checksum)


def payload_reading(
    left: RecordedRun, right: RecordedRun, *, profile: str, region: str
) -> CheckpointPayloadReading:
    """What S3 attests about the two payloads, which is what the record cannot say."""
    first = _attested(left, profile=profile, region=region)
    second = _attested(right, profile=profile, region=region)
    return CheckpointPayloadReading(
        left_size_bytes=first.size_bytes,
        right_size_bytes=second.size_bytes,
        left_crc32c=first.crc32c,
        right_crc32c=second.crc32c,
        **MEASURED_BYTE_DIVERGENCE,
    )


def build_evidence(
    left: RecordedRun,
    right: RecordedRun,
    *,
    observed_at: datetime,
    lineage_bucket: str,
    payloads: CheckpointPayloadReading | None,
) -> TwoRunEvidence:
    """The record, with every derivable part derived from the two runs in front of it."""
    differences = compare_runs(left, right)
    if unexplained(differences):
        raise CaptureFailedError(
            "comparison_has_unexplained_differences:" + ",".join(unexplained(differences))
        )
    coverage = required_field_coverage(left, right)
    if coverage.missing or coverage.unverified:
        raise CaptureFailedError("comparison_did_not_cover_the_required_fields")
    checkpoints = checkpoint_coverage(left, right)
    if checkpoints.unreadable:
        raise CaptureFailedError("comparison_found_an_unreadable_checkpoint")

    agreed = agreed_required_fields(left, right)
    digests = [one.value for one in agreed if one.path == "intent.manifest_sha256"]
    if not digests:
        raise CaptureFailedError("the_two_runs_do_not_share_a_manifest_digest")

    return TwoRunEvidence(
        observed_at=observed_at,
        schema_version=1,
        source="aws",
        environment="sandbox",
        lineage_bucket=lineage_bucket,
        manifest_sha256=json.loads(digests[0]),
        establishes=ESTABLISHES,
        does_not_establish=DOES_NOT_ESTABLISH,
        checkpoint_payloads=payloads,
        agreed=agreed,
        comparison=TwoRunComparison(
            schema_version=1,
            left=left.run_id,
            right=right.run_id,
            compared_at=observed_at,
            differences=tuple(
                ComparedField(
                    path=one.path,
                    left=one.left,
                    right=one.right,
                    cause=(found.name if (found := cause_for(one.path)) else None),
                )
                for one in differences
            ),
            unverified=coverage.unverified,
            unreadable_checkpoints=checkpoints.unreadable,
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--lineage-root", type=Path, required=True)
    parser.add_argument("--lineage-bucket", default="sbsandbox-intern-edullm-lineage")
    parser.add_argument("--left", default=THE_PAIR[0])
    parser.add_argument("--right", default=THE_PAIR[1])
    parser.add_argument("--profile", default="sbsandbox")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument(
        "--without-payload-reading",
        action="store_true",
        help="skip the two S3 heads, for a capture taken with no session",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    options = build_parser().parse_args(argv)
    if (options.left, options.right) != THE_PAIR:
        print(
            "error: this tool carries the caveats written about "
            f"{THE_PAIR[0]} and {THE_PAIR[1]}, and attaching them to another pair would "
            "make them a claim about runs nobody measured",
            file=sys.stderr,
        )
        return EXIT_UNUSABLE
    try:
        left = read_run(options.lineage_root, options.left)
        right = read_run(options.lineage_root, options.right)
        payloads = (
            None
            if options.without_payload_reading
            else payload_reading(
                left, right, profile=options.profile, region=options.region
            )
        )
        evidence = build_evidence(
            left,
            right,
            observed_at=observed_now(),
            lineage_bucket=options.lineage_bucket,
            payloads=payloads,
        )
        write_model(options.output, evidence, allow_content_digests=True)
    except CaptureFailedError as error:
        print(error.reason, file=sys.stderr)
        return EXIT_UNUSABLE
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_UNUSABLE

    report(
        {
            "written": options.output.name,
            "differences": len(evidence.comparison.differences),
            "agreed": len(evidence.agreed),
            "payloads_agree": (
                None if payloads is None else payloads.payloads_agree
            ),
        }
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
