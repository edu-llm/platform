"""What a committed Phase 4 capture is still worth, and how a test reads one.

The counterpart to :mod:`edullm_platform.capture_tooling`: that writes, this reads. Every
Phase 4 criterion rests on a test, and every one of those tests reads through here rather
than opening a JSON file, for one reason -- **an absent record must not read as a passing
check.** A test written as "load the file, assert the field" passes vacuously the moment
somebody moves the file, and the failure looks like a green suite.

So the reader distinguishes three answers and the tests act on all three: the record is
here and loads, the record is here and does not load, the record is not here. The second
and third are failures with different messages, because "somebody committed a broken
capture" and "nobody captured this" send a reader to different places.

**The two record kinds age differently and are read differently.** A run happened, so a
``RecordedEventModel`` never expires. A configuration statement does, and reading a stale
one raises rather than returning it -- the whole point of the window is that it stops a
capture reading as a claim about now.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from edullm_platform.capture_tooling import CAPTURE_SUFFIX
from edullm_platform.contracts.base import ContractModel
from edullm_platform.evidence import EVIDENCE_STALE_CODE, evidence_load_reason_code
from edullm_platform.phase4_evidence import (
    CheckpointObservation,
    GpuCapabilityEvidence,
    GpuComputeEnvironmentEvidence,
    GpuJobEvidence,
    InstanceTypeOfferingEvidence,
    OutputPrefixEvidence,
    SecretDeliveryEvidence,
    TrainingSummaryEvidence,
    WorkloadRoleScopeEvidence,
)

__all__ = [
    "BATCH_JOB_RECORD",
    "CAPTURE_ROOT",
    "CHECKPOINT_RECORD",
    "COMPUTE_ENVIRONMENT_RECORD",
    "GPU_CAPABILITY_RECORD",
    "OFFERINGS_RECORD",
    "OUTPUTS_RECORD",
    "ROLE_SCOPE_RECORD",
    "SECRET_DELIVERY_RECORD",
    "TRAINING_SUMMARY_RECORD",
    "CapturedRun",
    "MissingCaptureError",
    "UnreadableCaptureError",
    "captured_runs",
    "read_capture",
    "training_run",
]

#: Where committed captures live. Under ``fixtures/`` rather than the working directory,
#: because a record only becomes evidence once somebody has read it and copied it here.
CAPTURE_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "evidence" / "phase-4"

BATCH_JOB_RECORD = f"batch-job{CAPTURE_SUFFIX}"
CHECKPOINT_RECORD = f"checkpoint{CAPTURE_SUFFIX}"
GPU_CAPABILITY_RECORD = f"gpu-capability{CAPTURE_SUFFIX}"
TRAINING_SUMMARY_RECORD = f"training-summary{CAPTURE_SUFFIX}"
COMPUTE_ENVIRONMENT_RECORD = f"gpu-compute-environment{CAPTURE_SUFFIX}"
OFFERINGS_RECORD = f"instance-offerings{CAPTURE_SUFFIX}"
OUTPUTS_RECORD = f"outputs{CAPTURE_SUFFIX}"
SECRET_DELIVERY_RECORD = f"secret-delivery{CAPTURE_SUFFIX}"
ROLE_SCOPE_RECORD = f"workload-role-scope{CAPTURE_SUFFIX}"


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
            f"{path.relative_to(CAPTURE_ROOT.parent.parent.parent)} is not committed, so "
            "nothing here establishes what it would have said"
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
    """One GPU run's committed records, with the optional ones optional.

    Three runs are committed and they are deliberately not the same shape. One trained and
    has all four records; one was a capability probe and has no checkpoint; one failed
    before printing anything and has only its Batch record. A reader that required the full
    set would have to exclude two thirds of the evidence, and the failed run is the only
    thing establishing what a failure looks like here.
    """

    run_id: str
    job: GpuJobEvidence
    training: TrainingSummaryEvidence | None
    capability: GpuCapabilityEvidence | None
    checkpoint: CheckpointObservation | None

    @property
    def is_a_training_run(self) -> bool:
        return self.training is not None


def _optional[Record: ContractModel](path: Path, contract: type[Record]) -> Record | None:
    """A record that may legitimately not exist, but must load if it does.

    The distinction the two arms draw is the whole value: absent is an answer about the run
    -- it did not train, so it wrote no checkpoint -- and unreadable is never an answer
    about anything. Swallowing the second would turn a corrupt capture into a run that
    simply did less.
    """
    if not path.is_file():
        return None
    return read_capture(path, contract)


def captured_runs(root: Path = CAPTURE_ROOT) -> tuple[CapturedRun, ...]:
    """Every run with a committed capture, in run-id order.

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
                job=read_capture(entry / BATCH_JOB_RECORD, GpuJobEvidence),
                training=_optional(entry / TRAINING_SUMMARY_RECORD, TrainingSummaryEvidence),
                capability=_optional(entry / GPU_CAPABILITY_RECORD, GpuCapabilityEvidence),
                checkpoint=_optional(entry / CHECKPOINT_RECORD, CheckpointObservation),
            )
        )
    return tuple(found)


def training_run(root: Path = CAPTURE_ROOT) -> CapturedRun:
    """The one run that actually trained, refused loudly if there is not exactly one.

    Not "the first training run found". Several criteria are statements about *the* GPU
    training run, and if a second is ever committed those statements need re-reading rather
    than silently applying to whichever sorted first.
    """
    trained = [run for run in captured_runs(root) if run.is_a_training_run]
    if len(trained) != 1:
        raise MissingCaptureError(
            f"exactly one committed run must carry a training summary; found {len(trained)}"
        )
    return trained[0]


def compute_environment(root: Path = CAPTURE_ROOT) -> GpuComputeEnvironmentEvidence:
    return read_capture(root / COMPUTE_ENVIRONMENT_RECORD, GpuComputeEnvironmentEvidence)


def offerings(root: Path = CAPTURE_ROOT) -> InstanceTypeOfferingEvidence:
    return read_capture(root / OFFERINGS_RECORD, InstanceTypeOfferingEvidence)


def outputs(root: Path = CAPTURE_ROOT) -> OutputPrefixEvidence:
    return read_capture(root / OUTPUTS_RECORD, OutputPrefixEvidence)


def secret_delivery(root: Path = CAPTURE_ROOT) -> SecretDeliveryEvidence:
    return read_capture(root / SECRET_DELIVERY_RECORD, SecretDeliveryEvidence)


def role_scope(root: Path = CAPTURE_ROOT) -> WorkloadRoleScopeEvidence:
    return read_capture(root / ROLE_SCOPE_RECORD, WorkloadRoleScopeEvidence)
