"""Turn one reading of the account into the durations a submitter is shown beside a ceiling.

**THIS IS THE WIRING AND NOT A READER.** ``tools/read_substrate.py`` is the only thing that
touches the account on the instruments' behalf, and its header says why a second ingestion is
a mistake. This takes what that produced -- a substrate document, or a local lineage tree read
through the same collector -- and reduces it to
:mod:`edullm_platform.run_history`'s cohorts. Nothing here parses a lineage record.

**IT IS ALSO THE ONLY PLACE THE TWO HALVES MEET.** ``run_history`` names no substrate type, so
that ``edullm check`` does not pull the lineage reader into its import tree and therefore into
the release trigger. That decoupling has to be paid for somewhere and it is paid for here, in
one import and one call.

**WHY THE ANSWER IS A COMMITTED FILE RATHER THAN A LOOKUP.** ``edullm check`` reaches no
network, by design and by the absence of any credential it could use. So the measurement has
to travel with the tool, which means ``config/``, which is the directory the wheel carries. It
lands the way everything under ``config/`` lands, through a pull request somebody read, and
the document says when it was built and over how many runs so that a reader can discount an
old one.

**COVERAGE IS PRINTED BECAUSE IT IS THE FIRST THING TO ARGUE ABOUT.** A key of repository,
workload, machine and dataset is specific enough to match nothing against a store this size,
and the ladder in ``run_history`` is what answers for the rest. What fraction of the runs read
would get a figure if they were submitted again is the honest measure of whether the ladder
is worth having, and it is measured rather than asserted, because it depends entirely on how
concentrated the store happens to be this week.

Exit codes follow the repository's convention: 0 reported, 2 the inputs could not be read.
There is no 1, because this tool judges nothing and so has nothing to refuse.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

TOOLS_DIRECTORY = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIRECTORY.parent
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
if str(TOOLS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIRECTORY))

from read_substrate import collect
from report_run_costs import ReportInputError

from edullm_platform.capture_tooling import CaptureFailedError
from edullm_platform.config import load_yaml
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.run_history import (
    HISTORY_FILENAME,
    RUNGS,
    RUNS_FOR_A_FIGURE,
    RunHistory,
    as_document,
    coverage,
    elapsed_said,
    summarise,
)
from edullm_platform.substrate import Substrate, from_document

__all__ = [
    "EXIT_OK",
    "EXIT_UNUSABLE",
    "build_parser",
    "main",
    "read_substrate_document",
    "report",
]

EXIT_OK = 0
EXIT_UNUSABLE = 2


def read_substrate_document(path: Path) -> Substrate:
    """One reading off disk, refused rather than guessed at if this tree cannot read it."""
    return from_document(json.loads(path.read_text(encoding="utf-8")))


def report(history: RunHistory, substrate: Substrate) -> list[str]:
    """What was measured, in the order somebody deciding whether to commit it wants it.

    The cohorts that can answer are listed rather than counted, because the question this
    file exists to settle is which shapes a submitter gets a figure for, and a bare count of
    cohorts does not say.
    """
    answered, total = coverage(history, substrate.runs.values())
    lines = [
        f"{history.runs_read} run(s) read, {history.runs_with_a_duration} with a duration",
        (
            f"{answered} of {total} would be answered for if submitted again, at "
            f"{RUNS_FOR_A_FIGURE} successful run(s) per figure"
        ),
        f"{len(history.cohorts)} cohort(s) across {len(RUNGS)} rung(s)",
    ]
    quotable = [cohort for cohort in history.cohorts if cohort.answerable]
    if not quotable:
        lines.append(
            "no cohort reaches the bar, so every submission will be told there is no "
            "history for its shape. That is a finding about the store rather than a failure "
            "of this tool"
        )
        return lines
    lines.append("what a submitter would be told:")
    for cohort in quotable:
        assert cohort.median_seconds is not None  # answerable implies a success
        lines.append(
            f"  [{RUNGS[cohort.rung][1]}] {', '.join(cohort.key)}: "
            f"{cohort.succeeded} succeeded, {cohort.failed} failed, median "
            f"{elapsed_said(cohort.median_seconds)}"
        )
    return lines


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--reading",
        type=Path,
        default=None,
        help=(
            "a substrate document written by tools/read_substrate.py --write. Preferred, "
            "because it reaches no network and describes a reading somebody kept"
        ),
    )
    parser.add_argument("--lineage-root", type=Path, default=None)
    parser.add_argument("--config-dir", type=Path, default=PROJECT_ROOT / "config")
    parser.add_argument(
        "--write",
        type=Path,
        default=None,
        help=f"where to write the digest; defaults to <config-dir>/{HISTORY_FILENAME}",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="measure and print, and write nothing",
    )
    return parser


def _substrate(options: argparse.Namespace) -> Substrate:
    """The reading to summarise, from whichever of the two sources was named.

    A local lineage tree goes through ``read_substrate.collect`` in offline mode rather than
    being parsed here, so that a document built from records and one built from the account
    are the same code path with the same refusals.
    """
    if options.reading is not None:
        return read_substrate_document(options.reading)
    if options.lineage_root is None:
        raise ReportInputError(
            "nothing to read: pass --reading with a substrate document, or --lineage-root "
            "with a directory holding intent/ and attempt/"
        )
    catalog = load_yaml(options.config_dir / "workload-catalog.yaml", WorkloadCatalog)
    with tempfile.TemporaryDirectory() as scratch:
        return collect(
            scratch=Path(scratch),
            compute_profiles=catalog.compute_profiles,
            lineage_root=options.lineage_root,
            offline=True,
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Measure, print, and write the digest unless asked not to."""
    options = build_parser().parse_args(argv)
    try:
        substrate = _substrate(options)
    except (ReportInputError, CaptureFailedError, OSError, ValueError) as error:
        print(f"run_history_not_built: {error}", file=sys.stderr)
        return EXIT_UNUSABLE

    history = summarise(substrate.runs.values())
    if not options.dry_run:
        destination = options.write or (options.config_dir / HISTORY_FILENAME)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(as_document(history), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print("\n".join(report(history, substrate)))
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
