"""Two run ids in, exactly which fields differ out, and an exit code that can gate something.

**The spine's done-condition is that the synthetic job runs twice and the two lineage records
are identical except for time and id.** That sentence has to be checkable by somebody who was
not in the room, months later, against runs they did not submit. Read by eye it is two hundred
JSON leaves and a hope; run through this it is a table and an exit code.

Four exit codes, because there are four answers and no two of them may read alike:

0   Every difference has a name, and every field that must be equal is on both sides and equal.
1   Something differs that nothing explains, or a field the comparison requires is on one side
    and not the other. A finding about the runs.
2   The tree could not be read. Not a finding about the runs.
3   The runs agree about everything that was compared, and something required was not compared:
    a field neither record carries. Not a finding about the runs either, and not a pass.

**Exit 3 is here because the check for the fields that must be equal could not fail in the case
it was written for.** It gathered the paths the two records carried and required the required
ones among them to match, so a field absent from BOTH records was never gathered and was never
looked at -- and a field silently missing from both sides is indistinguishable, in a table of
differences, from the two runs agreeing about it. On a real July pair that was five fields,
``result.exit_code`` among them.

It is a code of its own rather than an exit 1 because the two causes of a field absent from
both records are not one thing. Either these records predate the field, which is what every
historical comparison will hit and is no finding at all, or the field should be there and is
not, which is. This tool cannot tell those apart and does not pretend to; what it can do is
refuse to report either of them as agreement. A caller that wants only the first kind waived
edits :data:`~edullm_platform.run_comparison.REQUIRED_FIELDS`, which is a deliberate act under
review rather than a flag on a command line.
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
    RequiredFieldCoverage,
    TwoRunComparison,
    cause_for,
    compare_runs,
    read_run,
    required_field_coverage,
    unexplained,
)

EXIT_MATCHED: Final = 0
EXIT_DIFFERED: Final = 1
EXIT_UNUSABLE: Final = 2
EXIT_UNVERIFIED: Final = 3

#: The fourth prefix, synced for its object count alone. read_run counts what is under
#: attempt/<run id>/ and never opens one.
SYNCED_PREFIXES: Final[tuple[str, ...]] = (*RECORD_PREFIXES, "attempt")

#: How much of a value one table cell may carry before the rest goes below the table.
#:
#: **Set above what a well-formed record carries and below what a submitter can type.**
#: Every leaf that differs between two runs of one submission is a run id, an attempt id, a
#: timestamp or an S3 URI; measured over the store's same-manifest pairs on 2026-08-04 the
#: longest was a hundred characters, and the longest the schema can produce is a checkpoint
#: success-marker URI at around a hundred and twenty. Those belong in the table, because
#: seeing the run id substituted into a prefix is the row a reader came for.
#:
#: A manifest command is the one shape with no bound. The longest in this store is seven
#: thousand characters, which is not a cell: it is eighty-odd wrapped lines holding one
#: row's content with every other row pushed off the screen. That is the only shape this
#: cuts, and it is why the section under the table is rare enough to be worth reading.
CELL_BUDGET: Final = 120

#: What replaces the tail of a value that did not fit. ASCII, so that a report pasted into a
#: terminal, an issue or a diff reads the same in all three.
ELLIPSIS: Final = "..."


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


def cell(value: str) -> str:
    """One value, fit for a table cell: cut to :data:`CELL_BUDGET` and its pipes escaped.

    Nothing is lost by this -- :func:`render` prints every value it cut in full underneath
    the table, and the JSON ``--output`` carries all of them untouched. The truncation is a
    property of one rendering and must not become a property of the comparison.

    Cut before escaping, so that a value ending mid ``\\|`` cannot leave a lone backslash
    eating the cell delimiter that follows it.
    """
    if len(value) > CELL_BUDGET:
        value = value[: CELL_BUDGET - len(ELLIPSIS)] + ELLIPSIS
    return value.replace("|", "\\|")


def render(comparison: TwoRunComparison, *, coverage: RequiredFieldCoverage) -> str:
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
        lines.append(f"| `{one.path}` | `{cell(one.left)}` | `{cell(one.right)}` | {why} |")
    summary = f"{len(comparison.differences)} field(s) differ."
    if comparison.unverified:
        summary += (
            f" {len(comparison.unverified)} field(s) the comparison requires were not "
            "checked at all."
        )
    lines += ["", summary]

    cut = [
        one
        for one in comparison.differences
        if len(one.left) > CELL_BUDGET or len(one.right) > CELL_BUDGET
    ]
    if cut:
        lines += ["", "### The values the table above had to cut short", ""]
        for one in cut:
            lines += [
                f"`{one.path}`",
                "",
                "```",
                f"left:  {one.left}",
                f"right: {one.right}",
                "```",
                "",
            ]
        lines.append(
            "A markdown table stops being one the moment a cell is wider than the "
            "terminal, and a manifest command is routinely wider than any terminal. "
            "These are the whole values, unabridged."
        )

    if coverage.missing:
        lines += [
            "",
            "### Fields the comparison requires and one of these runs does not carry",
            "",
            *(f"- `{path}`" for path in coverage.missing),
            "",
            (
                "A record that stopped carrying a field agrees with another record "
                "that also stopped carrying it, and that is not the same as agreeing."
            ),
        ]
    if comparison.unverified:
        lines += [
            "",
            "### Fields the comparison requires and NEITHER of these runs carries",
            "",
            *(f"- `{path}`" for path in comparison.unverified),
            "",
            (
                "Nothing above is a statement about these. Neither record carries them, so "
                "no difference could have been produced and the table's silence is not "
                "agreement. Two things cause this and they are opposite: the records may "
                "predate the field, in which case this comparison simply covers less than "
                "the list says it does, or the field belongs in these records and is not "
                "there, which is a finding. Nothing here can tell those apart -- a reader "
                "holding the two records and the schema they were written against can."
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
    coverage = required_field_coverage(left, right)
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
        unverified=coverage.unverified,
    )
    print(render(comparison, coverage=coverage), end="")
    if options.output:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(comparison.model_dump_json(indent=2) + "\n", encoding="utf-8")
    # A finding about the runs outranks a gap in the comparison, because the first is
    # actionable and the second is a caveat on how much of the first was looked for. Both
    # are printed whichever wins; only one can be a return value.
    if unexplained(differences) or coverage.missing:
        return EXIT_DIFFERED
    if coverage.unverified:
        return EXIT_UNVERIFIED
    return EXIT_MATCHED


if __name__ == "__main__":
    raise SystemExit(main())
