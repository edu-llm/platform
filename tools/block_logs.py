"""The tail of a capacity block run's log, for somebody who will never hold an AWS credential.

**THE ACCESS PROBLEM IS THE WHOLE PROBLEM.** The nodes already sync every run's ``train.log``
to S3 once a minute, so the log is durable and readable -- by the twenty people here who hold
an AWS role. The other fifteen have Weights and Biases and nothing else, which shows a loss
curve beautifully and cannot show a stack trace at all. So this holds the credential and prints
into a page: run by ``.github/workflows/block-logs.yml``, the output goes to the Actions job
summary, and reading it needs a repository login and nothing more.

**EVERY LOOKUP IS A BUCKET LISTING AND NONE OF THEM TOUCHES A NODE.** Asking the machine which
run it is holding is the obvious implementation and it stops working at 11:00 UTC on the
Tuesday, when there are no machines -- which is when this question starts being asked most.
S3 outlives the fleet, so S3 is what is asked.

**THE TAIL IS A RANGE READ.** Three days of training output is not a small object and
``aws s3 cp s3://... -`` fetches all of it to print the last two hundred lines. The size is read
first and only the end is pulled, which is the difference between a workflow that answers while
somebody is still looking at it and one that does not.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from edullm_platform.block_logs import (
    AmbiguousRunError,
    RunLog,
    choose_run,
    common_prefixes,
    log_key,
    logs_markdown,
    progress,
    read_candidates,
    tail,
    tail_range,
)
from edullm_platform.capture_tooling import CaptureFailedError, aws, aws_json

__all__ = ["build_parser", "main", "resolve"]

#: Where every block's outputs sit under the bucket. One segment, so that a listing delimited
#: at it answers "which blocks are in here" in one call.
BLOCK_ROOT: Final = "block"

#: A ceiling on how many runs are probed for a log when no node is named. A window is eight
#: machines and a weekend of iteration, so this is far above what a real fleet produces and is
#: here to bound what a mis-typed prefix costs rather than to be reached.
CANDIDATE_LIMIT: Final = 400


def _prefixes(
    *, bucket: str, prefix: str, profile: str | None, region: str
) -> tuple[str, ...]:
    listing = aws_json(
        [
            "s3api",
            "list-objects-v2",
            "--bucket",
            bucket,
            "--prefix",
            prefix,
            "--delimiter",
            "/",
        ],
        profile=profile,
        region=region,
    )
    return common_prefixes(listing)


def _head(
    *, bucket: str, key: str, profile: str | None, region: str
) -> Mapping[str, Any] | None:
    """What S3 says about one object, or nothing where there is no object.

    ``capture_tooling.aws`` rather than ``aws_json`` because absence is an ordinary answer
    here and not a failure: a run that was claimed and never started, or one whose container
    died before printing a line, has a prefix and no log under it. ``aws_json`` turns a
    non-zero exit into a raised capture failure, which is right for a call whose answer is
    required and wrong for a probe.
    """
    completed = aws(
        ["s3api", "head-object", "--bucket", bucket, "--key", key],
        profile=profile,
        region=region,
    )
    if completed.returncode != 0:
        return None
    try:
        described = json.loads(completed.stdout)
    except ValueError:
        return None
    return described if isinstance(described, Mapping) else None


def resolve(
    *,
    bucket: str,
    reservation_id: str | None,
    node: int | None,
    run: str | None,
    profile: str | None,
    region: str,
) -> tuple[str, RunLog]:
    """Which block, which node and which run this request is about.

    Each of the three narrows the search and none of them is required, which is deliberate:
    the person most likely to run this is the one who was told "look at node three" and knows
    nothing else. What is refused is a guess -- two live blocks, or one run name on two nodes,
    is an answer naming the alternatives rather than a coin flip. See
    ``edullm_platform.block_logs.choose_run``.
    """
    if reservation_id is None:
        live = _prefixes(
            bucket=bucket, prefix=f"{BLOCK_ROOT}/", profile=profile, region=region
        )
        if len(live) != 1:
            raise AmbiguousRunError(
                f"reservations_under_the_bucket:{len(live)}. Pass --reservation. "
                f"What is there: {', '.join(live) or 'nothing'}."
            )
        reservation_id = live[0]

    root = f"{BLOCK_ROOT}/{reservation_id}/"
    nodes: tuple[int, ...] = (
        (node,)
        if node is not None
        else tuple(
            int(name.removeprefix("node-"))
            for name in _prefixes(bucket=bucket, prefix=root, profile=profile, region=region)
            if name.startswith("node-") and name.removeprefix("node-").isdigit()
        )
    )

    described: list[tuple[int, str, str, Mapping[str, Any] | None]] = []
    for number in sorted(nodes):
        for name in _prefixes(
            bucket=bucket, prefix=f"{root}node-{number}/", profile=profile, region=region
        ):
            if len(described) >= CANDIDATE_LIMIT:
                break
            key = log_key(reservation=reservation_id, node=number, run=name)
            described.append(
                (number, name, key, _head(bucket=bucket, key=key, profile=profile, region=region))
            )

    return reservation_id, choose_run(read_candidates(described), run=run)


def fetch_tail(
    *, bucket: str, record: RunLog, lines: int, profile: str | None, region: str
) -> tuple[str, bool]:
    """The end of one log object, and whether the first line of it was cut in half.

    Written to a file and read back rather than captured from stdout, because the CLI writes
    the object body to the path it is given and prints its metadata on stdout -- so a caller
    that read stdout would get the metadata and call it the log.
    """
    start, length = tail_range(size=record.size, lines=lines)
    if length <= 0:
        return "", False
    with tempfile.TemporaryDirectory() as workspace:
        body = Path(workspace) / "tail"
        aws_json(
            [
                "s3api",
                "get-object",
                "--bucket",
                bucket,
                "--key",
                record.key,
                "--range",
                f"bytes={start}-{start + length - 1}",
                str(body),
            ],
            profile=profile,
            region=region,
        )
        return body.read_text(encoding="utf-8", errors="replace"), start > 0


def build_parser() -> argparse.ArgumentParser:
    """Named so ``tests/test_workflow_tool_arguments.py`` can import and read it."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--node", type=int, default=None, help="which node, 1 through 8")
    parser.add_argument(
        "--run",
        default=None,
        help="the run name. Left off, the most recently written log under the search is taken",
    )
    parser.add_argument("--lines", type=int, default=200)
    parser.add_argument("--reservation", default=None)
    parser.add_argument("--region", default="us-east-2")
    parser.add_argument("--bucket", default="edullm-block-outputs-us-east-2")
    parser.add_argument("--profile", default="sbsandbox")
    parser.add_argument(
        "--no-profile",
        dest="profile",
        action="store_const",
        const=None,
        help="use the ambient credentials, which is what a workflow runner has",
    )
    parser.add_argument(
        "--summary",
        default=None,
        help="append the page as markdown to this file, which is GITHUB_STEP_SUMMARY",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    # Bounded so that a request for a hundred thousand lines cannot produce a job summary
    # GitHub truncates in the middle of a traceback. The read itself is already capped by
    # `block_logs.MAXIMUM_TAIL_BYTES`; this caps what is rendered out of it.
    lines = max(min(arguments.lines, 5000), 1)
    try:
        reservation_id, record = resolve(
            bucket=arguments.bucket,
            reservation_id=arguments.reservation,
            node=arguments.node,
            run=arguments.run,
            profile=arguments.profile,
            region=arguments.region,
        )
        body, partial = fetch_tail(
            bucket=arguments.bucket,
            record=record,
            lines=lines,
            profile=arguments.profile,
            region=arguments.region,
        )
    except AmbiguousRunError as error:
        print(error.reason, file=sys.stderr)
        return 1
    except CaptureFailedError as error:
        print(error.reason, file=sys.stderr)
        return 2

    printed = tail(body, lines, partial_first_line=partial)
    page = logs_markdown(
        record,
        reservation=reservation_id,
        body=printed,
        measured=progress(printed),
        lines=lines,
        bucket=arguments.bucket,
    )
    if arguments.summary:
        with Path(arguments.summary).open("a", encoding="utf-8") as target:
            target.write(page + "\n")
    print(page)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
