"""What a committed Phase 5 capture is still worth, and how a test reads one.

The counterpart to :mod:`edullm_platform.capture_tooling`: that writes, this reads. Every
Phase 5 criterion about a person rests on a test, and every one of those tests reads through
here rather than opening a JSON file, for the reason Phase 4 recorded and this phase
inherits -- **an absent record must not read as a passing check.** A test written as "load
the file, assert the field" passes vacuously the moment somebody moves the file, and the
failure looks like a green suite.

So the reader distinguishes three answers and the tests act on all three: the record is here
and loads, the record is here and does not load, the record is not here. The second and
third are failures with different messages, because "somebody committed a broken capture"
and "nobody captured this" send a reader to different places.

**This is deliberately not a second copy of ``phase4_capture``.** The two share the
three-answer reader and nothing else worth sharing: Phase 4 assembles a run out of six
optional records and needs a dataclass to hold the shape, and a Phase 5 run is one record
that either loads or does not. Generalising over the pair would produce a base class whose
only content is :func:`read_capture`, which is nine lines, and would put the interesting
part -- what a run *is* in each phase -- behind a parameter. If a third phase wants the
reader, that is the moment to lift it into ``capture_tooling`` beside the write path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from edullm_platform.contracts.base import ContractModel
from edullm_platform.evidence import (
    CAPTURE_SUFFIX,
    EVIDENCE_STALE_CODE,
    evidence_load_reason_code,
)
from edullm_platform.phase5_evidence import (
    AdmittedRunEvidence,
    BranchProtectionEvidence,
    PublishedImageEvidence,
)

__all__ = [
    "ADMITTED_RUN_RECORD",
    "BRANCH_PROTECTION_RECORD",
    "CAPTURE_ROOT",
    "PUBLISHED_IMAGE_RECORD",
    "MissingCaptureError",
    "UnreadableCaptureError",
    "admitted_runs",
    "branch_protection",
    "published_image",
    "read_capture",
    "released_by_another_person",
]

#: Where committed captures live. Under ``fixtures/`` rather than the working directory,
#: because a record only becomes evidence once somebody has read it and copied it here.
CAPTURE_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "evidence" / "phase-5"

ADMITTED_RUN_RECORD = f"admitted-run{CAPTURE_SUFFIX}"
BRANCH_PROTECTION_RECORD = f"branch-protection{CAPTURE_SUFFIX}"
PUBLISHED_IMAGE_RECORD = f"published-image{CAPTURE_SUFFIX}"


class MissingCaptureError(FileNotFoundError):
    """Nobody captured this. Distinct from a capture that will not load, on purpose."""


class UnreadableCaptureError(ValueError):
    """The record is committed and does not load as the thing its name says it is.

    ``reason`` carries ``evidence_stale`` when the only problem is age, because a stale
    configuration record and a malformed one need different answers -- one is "run the
    capture again", the other is "this was never right".
    """

    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"{path.name} does not load: {reason}")

    @property
    def is_merely_stale(self) -> bool:
        return self.reason == EVIDENCE_STALE_CODE


def read_capture[Record: ContractModel](path: Path, contract: type[Record]) -> Record:
    """One committed capture, or a refusal that says which kind of nothing it found."""
    if not path.is_file():
        raise MissingCaptureError(
            f"{path.name} is not committed, so nothing here establishes what it would "
            "have said"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise UnreadableCaptureError(path, "not_json") from error
    try:
        return contract.model_validate(payload)
    except ValidationError as error:
        raise UnreadableCaptureError(path, evidence_load_reason_code(error)) from error


@dataclass(frozen=True)
class CapturedRun:
    """An admitted run's record, beside the name of the directory it was found in.

    A dataclass rather than a subclass of the record, deliberately. The directory name is a
    fact about where a file sits and not a field of the evidence, so making it one would put
    a filesystem detail into the published schema of a contract model and add a row to the
    complete inventory for something nothing ever serializes.

    ``run_id`` here is the *directory's* name, and the record carries its own. A caller
    asserting the two against each other is checking that a capture was not copied into the
    wrong place -- a mistake that otherwise produces evidence attributed to the wrong run
    and no error anywhere. This is the shape Phase 4 uses for the same reason.
    """

    run_id: str
    record: AdmittedRunEvidence


def admitted_runs(root: Path = CAPTURE_ROOT) -> tuple[CapturedRun, ...]:
    """Every pilot run with a committed capture, in run-id order.

    Discovered by walking ``runs/`` rather than from a list. A list would need editing
    whenever a run is added, and the failure of forgetting is a test that quietly stops
    reading the newest evidence while continuing to pass on the old.
    """
    directory = root / "runs"
    if not directory.is_dir():
        return ()
    found = []
    for entry in sorted(directory.iterdir()):
        if not entry.is_dir():
            continue
        found.append(
            CapturedRun(
                run_id=entry.name,
                record=read_capture(entry / ADMITTED_RUN_RECORD, AdmittedRunEvidence),
            )
        )
    return tuple(found)


def released_by_another_person(root: Path = CAPTURE_ROOT) -> tuple[CapturedRun, ...]:
    """Every committed run whose approver is not its submitter.

    A function rather than a comprehension at each call site, because this is the selection
    the phase is named after and three tests ask for it. Returning the runs rather than a
    count keeps the caller able to say *which* run, which is what a failure message needs.
    """
    return tuple(
        run for run in admitted_runs(root) if run.record.released_by_another_person
    )


def branch_protection(root: Path = CAPTURE_ROOT) -> BranchProtectionEvidence:
    return read_capture(root / BRANCH_PROTECTION_RECORD, BranchProtectionEvidence)


def published_image(root: Path = CAPTURE_ROOT) -> PublishedImageEvidence:
    return read_capture(root / PUBLISHED_IMAGE_RECORD, PublishedImageEvidence)
