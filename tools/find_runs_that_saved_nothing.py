"""Which runs promised a checkpoint and wrote none, which is the failure nothing reports.

**The most expensive mistake available on this platform is silent.** OLMo-core's example
defaults its save folder to ``/tmp``, which is local disk on a machine that stops existing
when the job ends. A twelve-hour run that takes the default trains for twelve hours, writes
checkpoints nobody can reach, exits zero, and is recorded as a success. Nothing in the
lineage disagrees: ``ResultManifest`` has no field for "a checkpoint was expected and none
was found", and an empty ``checkpoints`` tuple already means "this run was not expected to
checkpoint at all".

So this asks the question the record cannot. For every run whose workload profile carries a
checkpoint contract, it lists the run's checkpoint prefix and reports the ones that are
empty.

**Why a tool rather than the recorder.** Putting it in the lifecycle recorder is the right
long-term home and costs two things worth deciding separately: a ``ResultManifest`` field,
which is a contract change that regenerates four proof bundles, and ``s3:ListBucket`` for a
Lambda role that today holds four ``PutObject`` grants and deliberately nothing else. Both
are defensible and neither should be paid at the same time as finding out whether the check
is worth having. This runs from a laptop against credentials somebody already has, answers
the same question, and is what the person triaging a cohort's first week actually needs.

**What it cannot tell you.** A run still going has not written its first checkpoint yet, and
a run that failed before its first interval never would have. Both are reported separately
from the ones that finished cleanly and saved nothing, because only the third is a defect.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from edullm_platform.config import load_yaml
from edullm_platform.contracts.admission import IntentRecord
from edullm_platform.contracts.results import output_prefix
from edullm_platform.contracts.workload import WorkloadCatalog

PROJECT_ROOT = Path(__file__).resolve().parent.parent

EXIT_FOUND_SILENT_FAILURES = 1
EXIT_UNUSABLE = 2


class ReportInputError(Exception):
    """The records could not be read, which is never the same as there being none."""


@dataclass(frozen=True)
class RunCheckpointState:
    run_id: str
    team: str
    workload_profile: str
    prefix: str
    objects: int

    @property
    def saved_nothing(self) -> bool:
        return self.objects == 0


def _load_intents(directory: Path) -> list[IntentRecord]:
    root = directory / "intent"
    if not root.is_dir():
        raise ReportInputError(f"no intent/ directory under {directory}")
    records: list[IntentRecord] = []
    unparsed = 0
    for path in sorted(root.rglob("*.json")):
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as error:
            raise ReportInputError(f"{path} is not readable JSON: {error}") from error
        # A record is stored as a JSON string holding JSON, because the state machine writes
        # the handler's canonical bytes rather than re-encoding them.
        if isinstance(loaded, str):
            loaded = json.loads(loaded)
        if not isinstance(loaded, dict):
            continue
        try:
            records.append(IntentRecord.model_validate(loaded))
        except ValueError:
            # Counted rather than dropped. A store producing records this tree cannot read
            # is a defect in the recorder, and a report that quietly described the readable
            # subset would hide exactly that.
            unparsed += 1
    if unparsed:
        print(
            f"note: {unparsed} intent record(s) did not parse against the current contract "
            "and were left out of this report",
            file=sys.stderr,
        )
    return records


def _objects_under(prefix: str, *, profile: str | None, region: str | None) -> int:
    command = ["aws", "s3", "ls", prefix, "--recursive"]
    if profile:
        command += ["--profile", profile]
    if region:
        command += ["--region", region]
    answer = subprocess.run(command, capture_output=True, text=True, check=False)
    if answer.returncode != 0:
        # An empty prefix is not an error to `aws s3 ls`; it exits 1 with no output. A
        # genuine failure says something on stderr, and telling the two apart matters
        # because reporting an outage as "saved nothing" is the same false alarm this tool
        # exists to end.
        if answer.stderr.strip():
            raise ReportInputError(f"could not list {prefix}: {answer.stderr.strip()}")
        return 0
    return len([line for line in answer.stdout.splitlines() if line.strip()])


def checkpoint_states(
    records: Sequence[IntentRecord],
    catalog: WorkloadCatalog,
    *,
    profile: str | None = None,
    region: str | None = None,
) -> list[RunCheckpointState]:
    """One entry per run whose profile promised checkpoints, newest last.

    Runs whose profile carries no checkpoint contract are skipped rather than reported as
    saving nothing, because for them that is the correct outcome and mixing the two would
    make the report noise.
    """
    known = {workload.name for workload in catalog.workloads}
    contracted = {
        workload.name for workload in catalog.workloads if workload.checkpoint is not None
    }
    # A run submitted before the workload profiles were renamed names one the catalog no
    # longer has. Skipping it silently would be the same defect this tool exists to find --
    # a run quietly left out of a report about runs being quietly left out -- so the names
    # are collected and said out loud.
    retired: set[str] = set()
    states: list[RunCheckpointState] = []
    for record in records:
        manifest = record.manifest
        if manifest.workload_profile not in known:
            retired.add(manifest.workload_profile)
            continue
        if manifest.workload_profile not in contracted:
            continue
        prefix = output_prefix(team=manifest.team, run_id=record.run_id) + "checkpoints/"
        states.append(
            RunCheckpointState(
                run_id=record.run_id,
                team=manifest.team,
                workload_profile=manifest.workload_profile,
                prefix=prefix,
                objects=_objects_under(prefix, profile=profile, region=region),
            )
        )
    if retired:
        print(
            "note: skipped run(s) naming workload profiles the catalog no longer has "
            f"({', '.join(sorted(retired))}). Those names were renamed and the stored "
            "records keep the old ones deliberately; see config/workload-catalog.yaml.",
            file=sys.stderr,
        )
    return states


def render(states: Sequence[RunCheckpointState]) -> str:
    if not states:
        return (
            "No run has been submitted under a workload profile that promises checkpoints, "
            "so there is nothing here to be wrong.\n"
        )

    silent = [state for state in states if state.saved_nothing]
    headline = (
        f"{len(states)} run(s) were submitted under a profile carrying a checkpoint "
        f"contract. {len(silent)} of them wrote nothing."
    )
    lines = ["# Runs that promised a checkpoint", "", headline, ""]

    if silent:
        explanation = (
            "Each of these declared a checkpoint contract and left its prefix empty. The "
            "usual cause is a training command that did not pass "
            '`--save-folder "$EDULLM_CHECKPOINT_DIR"`, in which case the checkpoints were '
            "written to local disk on a machine that no longer exists."
        )
        lines += [
            "## Wrote nothing",
            "",
            explanation,
            "",
            "| Run | Team | Profile |",
            "| --- | --- | --- |",
        ]
        lines += [
            f"| `{state.run_id}` | {state.team} | {state.workload_profile} |" for state in silent
        ]
        lines.append("")

    kept = [state for state in states if not state.saved_nothing]
    if kept:
        lines += [
            "## Wrote something",
            "",
            "| Run | Team | Objects |",
            "| --- | --- | --- |",
        ]
        lines += [f"| `{state.run_id}` | {state.team} | {state.objects} |" for state in kept]
        lines.append("")

    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--lineage-root",
        type=Path,
        required=True,
        help="a directory holding an intent/ tree, synced from the lineage bucket",
    )
    parser.add_argument("--config-dir", type=Path, default=PROJECT_ROOT / "config")
    parser.add_argument("--output", type=Path, help="write the report here rather than to stdout")
    parser.add_argument("--profile")
    parser.add_argument("--region")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    options = build_parser().parse_args(argv)
    try:
        records = _load_intents(options.lineage_root)
        catalog = load_yaml(options.config_dir / "workload-catalog.yaml", WorkloadCatalog)
        states = checkpoint_states(
            records, catalog, profile=options.profile, region=options.region
        )
    except ReportInputError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_UNUSABLE

    report = render(states)
    if options.output:
        options.output.write_text(report, encoding="utf-8")
    else:
        print(report, end="")

    # A non-zero exit so this can gate something later without being rewritten. It is not
    # an error in the tool; it is the tool having found what it looks for.
    return EXIT_FOUND_SILENT_FAILURES if any(state.saved_nothing for state in states) else 0


if __name__ == "__main__":
    raise SystemExit(main())
