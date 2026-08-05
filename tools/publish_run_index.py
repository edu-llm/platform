"""Add one run to the index, at the moment its id exists and not a search later.

**THIS TOOL MERGES AND NEVER REPLACES, AND THE GUARD BELOW IS THE WHOLE OF WHY IT IS A TOOL
RATHER THAN THREE LINES OF SHELL.** The index is rewritten whole and force-pushed, which is
safe only because the document is cumulative: the tip carries every run. A writer that met an
unreadable file and started a new index would publish one entry and force-push away every
mapping the branch held -- and the mappings it destroyed are the ones nothing else has, since
the whole point of writing them at mint time is that they cannot be reconstructed afterwards.
So an existing file that will not parse stops the tool, and only an absent file is allowed to
mean an empty index.

Every value arrives through the environment rather than through the command line, so nothing
a submitter types is ever part of a shell word. Exit codes follow the repository convention:
0 written, 2 an input could not be read. There is no 1, because this tool judges nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

TOOLS_DIRECTORY = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIRECTORY.parent
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from edullm_platform.run_index import (
    RUN_INDEX_PATH,
    MintedRun,
    RunIndexFormatError,
    as_document,
    from_document,
    merged,
)

__all__ = [
    "EXIT_OK",
    "EXIT_UNUSABLE",
    "build_parser",
    "main",
    "minted_from_environment",
    "publish",
]

EXIT_OK = 0
EXIT_UNUSABLE = 2

#: Which environment variable carries which field. Named here rather than read ad hoc so
#: that the workflow and this tool cannot come to disagree about a spelling: a variable this
#: does not read is an empty string, and an empty string in an index entry is a mapping that
#: resolves to nothing rather than an error anybody sees.
REQUIRED = {
    "run_id": "RUN_ID",
    "workflow_run_url": "WORKFLOW_RUN_URL",
    "submitter": "SUBMITTER",
    "repository": "RESEARCH_REPOSITORY",
    "commit_sha": "COMMIT_SHA",
    "team": "TEAM",
    "compute_profile": "COMPUTE_PROFILE",
    "approval_class": "APPROVAL_CLASS",
}


class RunIndexInputError(RuntimeError):
    """An input this could not read, which is never a run that does not need indexing."""


def _required(variable: str) -> str:
    value = os.environ.get(variable, "").strip()
    if not value:
        raise RunIndexInputError(f"{variable} is unset, so the entry would name nothing")
    return value


def _optional_whole(variable: str) -> int | None:
    text = os.environ.get(variable, "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        raise RunIndexInputError(f"{variable} must be a whole number and carries {text!r}") from None


def _whole(variable: str) -> int:
    value = _optional_whole(variable)
    if value is None:
        raise RunIndexInputError(f"{variable} is unset, so the entry would join to nothing")
    return value


def minted_from_environment(*, minted_at: datetime | None = None) -> MintedRun:
    """One entry, out of what the compile job knows and the runner supplies.

    The experiment and the fan-out size are the only optional fields, and both are optional
    on the submission form. Everything else is required by admission, so an entry missing one
    of them is a workflow that changed shape rather than a submission that omitted something.
    """
    fields = {name: _required(variable) for name, variable in REQUIRED.items()}
    return MintedRun(
        workflow_run_id=_whole("WORKFLOW_RUN_ID"),
        experiment=os.environ.get("EXPERIMENT", "").strip() or None,
        fanout_size=_optional_whole("FANOUT_SIZE"),
        minted_at=minted_at or datetime.now(UTC),
        **fields,
    )


def publish(index: Path, arriving: MintedRun) -> tuple[int, bool]:
    """Merge one run into the index on disk, answering how many it holds and whether it grew.

    AN ABSENT FILE IS AN EMPTY INDEX AND AN UNREADABLE ONE IS NOT. Those are the same zero
    entries and only one of them is a fact: the branch on its first day genuinely holds
    nothing, and a file that will not parse is a document somebody has to look at before
    anything force-pushes over it.
    """
    existing: tuple[MintedRun, ...] = ()
    if index.exists():
        try:
            existing = from_document(json.loads(index.read_text(encoding="utf-8")))
        except (RunIndexFormatError, ValueError, KeyError, TypeError) as error:
            raise RunIndexInputError(
                f"{index} exists and could not be read ({error}). Publishing now would "
                "force-push a one-entry index over every mapping the branch holds, and "
                "those mappings cannot be reconstructed: that is what writing them at mint "
                "time is for."
            ) from error

    runs = merged(existing, arriving)
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(
        json.dumps(as_document(runs), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return len(runs), len(runs) > len(existing)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--index",
        type=Path,
        default=Path(RUN_INDEX_PATH),
        help="the index document, which is created when it is not there and never replaced",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    options = build_parser().parse_args(argv)
    try:
        arriving = minted_from_environment()
        held, grew = publish(options.index, arriving)
    except (RunIndexInputError, OSError) as error:
        print(f"run_index_not_written: {error}", file=sys.stderr)
        return EXIT_UNUSABLE
    if grew:
        print(f"{arriving.run_id} is run {held} in the index.")
    else:
        print(f"{arriving.run_id} was already indexed; the index holds {held}.")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
