"""The index from a platform run id to the workflow run that minted it.

**NOTHING ELSE CAN SUPPLY THIS AND THAT IS THE WHOLE ARGUMENT FOR IT.** A workflow run does
not expose the inputs it was dispatched with, and ``run-name`` is evaluated when the run
starts -- before the compile job exists, let alone before it mints an id -- so the one field
that would show the id on the runs list cannot carry it. What is left, and what the CLI does
today, is to read each recent dispatch's compiled manifest and stop at the first match: one
artifact download per candidate examined, over a bounded window.

**IT DEGRADES EXACTLY WHERE IT IS NEEDED MOST.** A run submitted an hour ago is one or two
downloads away. A run from last month is past the window, and its artifacts have aged out
anyway, so the search cannot find it at all -- and those are precisely the runs people ask
about, because they are the ones nobody remembers. The join is reconstructed after the fact
by a search that gets worse the older the question is.

**SO IT IS WRITTEN AT MINT TIME INSTEAD.** The compile job knows both halves the moment the
second one exists: ``github.run_id`` is the workflow run it is in, and the id it just minted
is the other. Writing the pair then makes the index authoritative at birth. It also covers
the runs the lineage store never hears about -- a submission refused at compile time or
parked at a gate forever writes no intent record, and an intent record is where every other
copy of this join lives.

**ONE FILE, REWRITTEN, RATHER THAN ONE FILE PER RUN.** An abbreviated run id has to be
resolved against every entry, and a directory listing costs a call per lookup and caps at a
thousand entries. One document is one authenticated read whatever the id looks like, and it
diffs as one line per submission.

**THE ENTRY IS WHAT GITHUB KNEW AT MINT TIME AND NOTHING ELSE.** No state, no cost, no
outcome. Those change, and this does not: a run id is minted once. Anything that changes
belongs to the substrate reading, which has a different cadence and a different writer, and
mixing the two would mean a state refresh could overwrite a mapping.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

__all__ = [
    "RUN_INDEX_BRANCH",
    "RUN_INDEX_FORMAT_VERSION",
    "RUN_INDEX_PATH",
    "MintedRun",
    "RunIndexFormatError",
    "as_document",
    "from_document",
    "merged",
]

#: Where the index lives. An orphan branch under ``machine/`` so that it does not read as
#: somebody's feature branch, and off ``main`` because branch protection wants an approving
#: review and a code-owner review on every commit there -- buying an exception to that in
#: order to publish an index is the wrong trade. Spelled here rather than in the workflow
#: alone because the CLI reads the same ref and a rename on one side is a lookup that finds
#: nothing and reports the run as unknown.
RUN_INDEX_BRANCH: Final = "machine/run-index"

#: The one file on that branch.
RUN_INDEX_PATH: Final = "run-index.json"

#: Bumped when an entry stops meaning what it meant. A reader that met a newer document and
#: took the fields it recognised would report the rest as absent, and absent is the meaning
#: nothing in this pipeline is allowed to invent.
RUN_INDEX_FORMAT_VERSION: Final = 1


class RunIndexFormatError(ValueError):
    """A document this tree cannot read, which is never an index that holds nothing."""


@dataclass(frozen=True)
class MintedRun:
    """One run id, and everything GitHub knew about it the moment it existed.

    Frozen, and every field is settled at mint time. A field that could change later does
    not belong here: this document is rewritten whole on every submission, so a mutable
    field would be a value one writer could take back from another.
    """

    run_id: str
    workflow_run_id: int
    workflow_run_url: str
    submitter: str
    repository: str
    commit_sha: str
    team: str
    experiment: str | None
    compute_profile: str
    approval_class: str
    fanout_size: int | None
    minted_at: datetime

    def as_entry(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "workflow_run_id": self.workflow_run_id,
            "workflow_run_url": self.workflow_run_url,
            "submitter": self.submitter,
            "repository": self.repository,
            "commit_sha": self.commit_sha,
            "team": self.team,
            "experiment": self.experiment,
            "compute_profile": self.compute_profile,
            "approval_class": self.approval_class,
            "fanout_size": self.fanout_size,
            "minted_at": self.minted_at.isoformat(),
        }

    @classmethod
    def of_entry(cls, entry: Mapping[str, Any]) -> MintedRun:
        return cls(
            run_id=str(entry["run_id"]),
            workflow_run_id=int(entry["workflow_run_id"]),
            workflow_run_url=str(entry["workflow_run_url"]),
            submitter=str(entry["submitter"]),
            repository=str(entry["repository"]),
            commit_sha=str(entry["commit_sha"]),
            team=str(entry["team"]),
            experiment=entry["experiment"],
            compute_profile=str(entry["compute_profile"]),
            approval_class=str(entry["approval_class"]),
            fanout_size=entry["fanout_size"],
            minted_at=datetime.fromisoformat(str(entry["minted_at"])),
        )


def as_document(runs: Iterable[MintedRun]) -> dict[str, Any]:
    """The index, ordered newest first so a person opening it sees this morning."""
    ordered = sorted(runs, key=lambda minted: (minted.minted_at, minted.run_id), reverse=True)
    return {
        "format_version": RUN_INDEX_FORMAT_VERSION,
        "runs": [minted.as_entry() for minted in ordered],
    }


def from_document(document: Mapping[str, Any]) -> tuple[MintedRun, ...]:
    """Every entry, refusing a format this tree does not know."""
    version = document.get("format_version")
    if version != RUN_INDEX_FORMAT_VERSION:
        raise RunIndexFormatError(
            f"this tree reads run index format {RUN_INDEX_FORMAT_VERSION} and the document "
            f"declares {version!r}"
        )
    entries = document.get("runs")
    if not isinstance(entries, list):
        raise RunIndexFormatError("the run index carries no list of runs")
    return tuple(MintedRun.of_entry(entry) for entry in entries)


def merged(existing: Iterable[MintedRun], arriving: MintedRun) -> tuple[MintedRun, ...]:
    """The index with one more run in it, and the earlier entry kept where they collide.

    THE EARLIER ENTRY WINS, WHICH IS THE OPPOSITE OF WHAT A CACHE WOULD DO. A run id is
    minted once, so a second entry for one id is a re-run of a workflow rather than a newer
    truth -- and the workflow run that first minted it is the one that carries the compile
    log, the approver and the artifacts. Overwriting would point the index at a re-run that
    minted a different id and recorded nothing about this one.
    """
    kept = {minted.run_id: minted for minted in existing}
    kept.setdefault(arriving.run_id, arriving)
    return tuple(kept.values())
