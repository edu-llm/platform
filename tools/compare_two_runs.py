"""Two run ids in, exactly which fields differ out, and an exit code that can gate something.

**The spine's done-condition is that the synthetic job runs twice and the two lineage records
are identical except for time and id.** That sentence has to be checkable by somebody who was
not in the room, months later, against runs they did not submit. Read by eye it is two hundred
JSON leaves and a hope; run through this it is a table and an exit code.

Exit 0 says every difference has a name and every field that must be equal is present on both
sides and equal. Exit 1 says something differs that nothing explains, or a field the comparison
requires is missing from one of them. Exit 2 says the tree could not be read, which is not a
finding about the runs.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from edullm_platform.run_comparison import (
    RECORD_PREFIXES,
    ComparedField,
    TwoRunComparison,
    cause_for,
    compare_runs,
    identical_fields_missing,
    read_run,
    unexplained,
)

EXIT_MATCHED: Final = 0
EXIT_DIFFERED: Final = 1
EXIT_UNUSABLE: Final = 2

#: The fourth prefix, synced for its object count alone. read_run counts what is under
#: attempt/<run id>/ and never opens one.
SYNCED_PREFIXES: Final[tuple[str, ...]] = (*RECORD_PREFIXES, "attempt")


def sync(bucket: str, root: Path, *, profile: str | None, region: str | None) -> None:
    for prefix in SYNCED_PREFIXES:
        command = ["aws", "s3", "sync", f"s3://{bucket}/{prefix}/", str(root / prefix), "--quiet"]
        if profile:
            command += ["--profile", profile]
        if region:
            command += ["--region", region]
        # check=False and the return code read by hand, because the CLI's stderr is not
        # reproduced: an AWS error body routinely names an ARN, and an ARN carries the
        # account id. The prefix that could not be read is the whole of what a reader needs.
        finished = subprocess.run(command, capture_output=True, text=True, check=False)
        if finished.returncode != 0:
            raise OSError(f"could not read s3://{bucket}/{prefix}/")


def render(comparison: TwoRunComparison, *, required_missing: Sequence[str]) -> str:
    lines = [
        "## Two runs of one submission",
        "",
        f"Left:  `{comparison.left}`",
        f"Right: `{comparison.right}`",
        "",
        "| field | left | right | why it differs |",
        "| --- | --- | --- | --- |",
    ]
    for one in comparison.differences:
        why = one.cause or "**nothing explains this**"
        lines.append(f"| `{one.path}` | `{one.left}` | `{one.right}` | {why} |")
    lines += ["", f"{len(comparison.differences)} field(s) differ."]
    if required_missing:
        lines += [
            "",
            "### Fields the comparison requires and one of these runs does not carry",
            "",
            *(f"- `{path}`" for path in required_missing),
            "",
            (
                "A record that stopped carrying a field agrees with another record "
                "that also stopped carrying it, and that is not the same as agreeing."
            ),
        ]
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--lineage-root", type=Path, required=True)
    parser.add_argument("--left", required=True)
    parser.add_argument("--right", required=True)
    parser.add_argument("--sync-from-bucket")
    parser.add_argument("--profile")
    parser.add_argument("--region")
    parser.add_argument("--output", type=Path, help="write the comparison here as JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    options = build_parser().parse_args(argv)
    if options.left == options.right:
        print("error: both ids name the same run, which establishes nothing", file=sys.stderr)
        return EXIT_UNUSABLE
    try:
        if options.sync_from_bucket:
            sync(
                options.sync_from_bucket,
                options.lineage_root,
                profile=options.profile,
                region=options.region,
            )
        left = read_run(options.lineage_root, options.left)
        right = read_run(options.lineage_root, options.right)
    except (OSError, ValueError) as error:
        # Printed unmasked, and that is safe only because every message reaching here is
        # ours. `sync` deliberately does not reproduce the CLI's stderr and `read_run` names
        # a prefix and a run id. Passing this through `redact_aws_account_ids` was tried and
        # is wrong: that function REFUSES text carrying anything credential-shaped rather
        # than masking it, and a run id is thirty-six characters of hex and hyphens, so the
        # masker raises and the tool reports a redaction failure instead of the missing file.
        print(f"error: {error}", file=sys.stderr)
        return EXIT_UNUSABLE

    differences = compare_runs(left, right)
    comparison = TwoRunComparison(
        schema_version=1,
        left=left.run_id,
        right=right.run_id,
        compared_at=datetime.now(UTC),
        differences=tuple(
            ComparedField(
                path=one.path,
                left=one.left,
                right=one.right,
                cause=(found.name if (found := cause_for(one.path)) else None),
            )
            for one in differences
        ),
    )
    required_missing = identical_fields_missing(left, right)
    print(render(comparison, required_missing=required_missing), end="")
    if options.output:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(comparison.model_dump_json(indent=2) + "\n", encoding="utf-8")
    if unexplained(differences) or required_missing:
        return EXIT_DIFFERED
    return EXIT_MATCHED


if __name__ == "__main__":
    raise SystemExit(main())
