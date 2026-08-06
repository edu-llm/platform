"""Two run ids in, exactly which fields differ out, and an exit code that can gate something.

**The spine's done-condition is that the synthetic job runs twice and the two lineage records
are identical except for time and id.** That sentence has to be checkable by somebody who was
not in the room, months later, against runs they did not submit. Read by eye it is two hundred
JSON leaves and a hope; run through this it is a table and an exit code.

Four exit codes, because there are four answers and no two of them may read alike:

0   Every difference has a name, and every field that must be equal is on both sides and equal.
1   Something differs that nothing explains, a field the comparison requires is on one side and
    not the other, or a run wrote a checkpoint into a directory no layout could read. A finding
    about the runs.
2   The tree could not be read. Not a finding about the runs.
3   The runs agree about everything that was compared, and something required was not compared:
    a field neither record carries, or a checkpoint whose record says it read no digest of the
    payload. Not a finding about the runs either, and not a pass.

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

**A CHECKPOINT DIRECTORY NOTHING COULD READ IS AN EXIT 1 AND NOT AN EXIT 3, AND THE
DISTINCTION IS THE ONE THE PARAGRAPH ABOVE DRAWS.** Exit 3 exists for an absence this tool
cannot attribute. This absence is attributed: the record names the directory in
``checkpoint_survey.unparsed_directories``, the run demonstrably wrote objects into it, and
the record therefore understates what the run produced. That is present, nameable and worth
acting on, which is what exit 1 means here. Measured on ``run_019fd2c9`` and ``run_019fd2ca``,
which each wrote 762 MB into ``step-20/`` and exited 0 through this tool over ten named
differences, none of them a checkpoint field.

**What is printed and does not change the exit code is the payload digest.** Every checkpoint
in the store was recorded by a listing, and a listing cannot open the ``_SUCCESS`` that
carries the digest of the bytes. So a checkpoint compared here was compared on its step, its
size and a description of its listing, and two payloads that differ leave no trace in any of
the three. That is the ordinary state of every record in the store rather than a defect in
these two runs, so it is a caveat printed loudly and not a refusal.
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
    CheckpointCoverage,
    ComparedField,
    RequiredFieldCoverage,
    TwoRunComparison,
    cause_for,
    checkpoint_coverage,
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


def render(
    comparison: TwoRunComparison,
    *,
    coverage: RequiredFieldCoverage,
    checkpoints: CheckpointCoverage,
) -> str:
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
    if checkpoints.is_blocked:
        summary += (
            f" {len(checkpoints.unreadable)} checkpoint directory(s) could not be read, so "
            "no checkpoint was compared at all."
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
    lines += checkpoint_section(checkpoints)
    return "\n".join(lines) + "\n"


def checkpoint_section(checkpoints: CheckpointCoverage) -> list[str]:
    """What the checkpoint half of this comparison could not do, said rather than left blank.

    Two headings and never both, because they describe the same absence at two depths and
    printing both would bury the one that is a defect under the one that is routine.
    """
    if checkpoints.is_blocked:
        return [
            "",
            "### THE CHECKPOINT COMPARISON DID NOT RUN",
            "",
            *(
                f"- `{one.run_id}` wrote `{one.directory}`, which no layout could read a "
                "checkpoint out of"
                for one in checkpoints.unreadable
            ),
            "",
            (
                "A directory the recorder could not name a step for is a directory it "
                "recorded no checkpoint from, so the record's `checkpoints` list is empty "
                "and every checkpoint field in the table above is missing from both sides. "
                "That reads exactly like two runs agreeing about their checkpoints and it "
                "is the opposite: nothing was compared. The run wrote something, "
                "`checkpoint_survey.objects_seen` says how much, and the layout it wrote it "
                "in is the name above. Fix the layout or fix the matcher, and run this "
                "again. Until then this comparison says nothing whatever about checkpoints."
            ),
        ]
    if not checkpoints.compared:
        return []
    lines = ["", "### What the checkpoint comparison saw of the bytes", ""]
    if checkpoints.payloads_read:
        lines.append(
            f"{checkpoints.payloads_read} of {checkpoints.compared} checkpoint(s) carry a "
            "digest of the payload on both sides, so the table above compared what is in "
            "them and not only what they are called. A `payload.objects[].digest` row is "
            "two runs holding different bytes, which is ORDINARY between two runs of one "
            "submission -- the order a GPU reduces in is not fixed, so identical code on "
            "identical data writes different bytes. It is named as a cause, it changes no "
            "exit code, and nothing in this platform retries or refuses because of it. "
            "What is not excused is a difference in an object's name or size: that is a "
            "truncated write and it is a finding."
        )
    if checkpoints.payloads_absent:
        lines.append(
            f"{checkpoints.payloads_absent} of {checkpoints.compared} checkpoint(s) carry "
            "no payload reading at all, because both records were written before "
            "`CheckpointManifest.payload` existed. Those were compared on step, size and "
            "`checksum`, and `checksum` is a SHA-256 over the names and sizes the listing "
            "returned rather than over the bytes -- so for those entries a table with no "
            "`checksum` row in it is still not a statement that the two checkpoints hold "
            "the same weights. Re-running the recorder over the same prefix is what closes "
            "it; nothing rewrites a record that is already written."
        )
    if checkpoints.unattested:
        lines.append(
            "The following checkpoint(s) carry a payload reading that read no digest, so "
            "what is in them was NOT compared and their silence is not agreement:"
        )
        lines += [
            f"- `{one.run_id}` at `{one.checkpoint}` reports `{one.outcome}`"
            for one in checkpoints.unattested
        ]
        lines.append(
            "`refused` is the live answer until the lifecycle role holds "
            "`s3:GetObjectAttributes`; `not_attempted` is a projection built with no store "
            "behind it; `too_many_objects` is a checkpoint wider than the record's ceiling. "
            "None of the three is a fault of the run, and none of them is agreement either, "
            "which is why this exits UNVERIFIED rather than matched."
        )
    return lines


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
    checkpoints = checkpoint_coverage(left, right)
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
        unreadable_checkpoints=checkpoints.unreadable,
        unattested_payloads=checkpoints.unattested,
    )
    print(render(comparison, coverage=coverage, checkpoints=checkpoints), end="")
    if options.output:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(comparison.model_dump_json(indent=2) + "\n", encoding="utf-8")
    # A finding about the runs outranks a gap in the comparison, because the first is
    # actionable and the second is a caveat on how much of the first was looked for. Both
    # are printed whichever wins; only one can be a return value.
    #
    # A checkpoint directory nothing could read is in the first group and not the second.
    # It is not an absence this tool has to guess the cause of: the record names the
    # directory, the survey says the run wrote objects into it, and the checkpoint half of
    # the done-condition therefore did not happen. See the module docstring.
    if unexplained(differences) or coverage.missing or checkpoints.is_blocked:
        return EXIT_DIFFERED
    # A record that carries a payload reading and says it read no digest is the same shape
    # of gap as a required field neither record carries: something the comparison claims to
    # cover was not covered, and reporting it as agreement is the one thing this tool must
    # not do. A record with no payload reading AT ALL is deliberately not here -- that is
    # every record written before the field existed, and failing to verify history that
    # could not have been recorded is not a finding about anything.
    if coverage.unverified or checkpoints.payloads_unverified:
        return EXIT_UNVERIFIED
    return EXIT_MATCHED


if __name__ == "__main__":
    raise SystemExit(main())
