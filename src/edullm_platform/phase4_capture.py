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

from edullm_platform.contracts.base import ContractModel
from edullm_platform.evidence import (
    CAPTURE_SUFFIX,
    EVIDENCE_STALE_CODE,
    evidence_load_reason_code,
)
from edullm_platform.phase1_capture import CommittedRoleCapture, read_committed_role_captures
from edullm_platform.phase4_evidence import (
    CheckpointObservation,
    CorpusReadEvidence,
    GpuCapabilityEvidence,
    GpuComputeEnvironmentEvidence,
    GpuJobEvidence,
    InstanceTypeOfferingEvidence,
    IsolationEvidence,
    OutputPrefixEvidence,
    ResumeEvidence,
    SecretDeliveryEvidence,
    TrainingSummaryEvidence,
    WorkloadRoleScopeEvidence,
)
from edullm_platform.role_drift import PHASE4_ROLE_TEMPLATES

__all__ = [
    "BATCH_JOB_RECORD",
    "CAPTURE_ROOT",
    "CHECKPOINT_RECORD",
    "COMPUTE_ENVIRONMENT_RECORD",
    "CORPUS_READ_RECORD",
    "GPU_CAPABILITY_RECORD",
    "ISOLATION_RECORD",
    "OFFERINGS_RECORD",
    "OUTPUTS_RECORD",
    "RESUME_RECORD",
    "ROLE_CAPTURE_DIR",
    "ROLE_SCOPE_RECORD",
    "SECRET_DELIVERY_RECORD",
    "TRAINING_SUMMARY_RECORD",
    "CapturedRun",
    "MissingCaptureError",
    "UnreadableCaptureError",
    "captured_runs",
    "read_capture",
    "role_captures",
    "training_run",
]

#: The checkout, so a reader can resolve a template path a registry gives it relative to it.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: Where committed captures live. Under ``fixtures/`` rather than the working directory,
#: because a record only becomes evidence once somebody has read it and copied it here.
CAPTURE_ROOT = REPO_ROOT / "fixtures" / "evidence" / "phase-4"

#: Where the captures of :data:`~edullm_platform.role_drift.PHASE4_ROLE_TEMPLATES` are
#: committed. A directory of its own, and the reason is mechanical: the reader below reports
#: in both directions -- a registered role with no capture, and a capture the registry does
#: not declare -- so a directory is implicitly owned by exactly one registry, and a stray
#: file in it reads as a role nobody declared rather than as a filing mistake.
ROLE_CAPTURE_DIR = CAPTURE_ROOT / "roles"

BATCH_JOB_RECORD = f"batch-job{CAPTURE_SUFFIX}"
CHECKPOINT_RECORD = f"checkpoint{CAPTURE_SUFFIX}"
CORPUS_READ_RECORD = f"corpus-read{CAPTURE_SUFFIX}"
GPU_CAPABILITY_RECORD = f"gpu-capability{CAPTURE_SUFFIX}"
ISOLATION_RECORD = f"isolation{CAPTURE_SUFFIX}"
RESUME_RECORD = f"resume{CAPTURE_SUFFIX}"
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

    The committed runs are deliberately not the same shape. One read a published corpus and
    carries every record; two trained on tokens they generated and carry no corpus; one was
    a capability probe and has no checkpoint; one failed before printing anything and has
    only its Batch record. A reader that required the full set would have to exclude most of
    the evidence, and the failed run is the only thing establishing what a failure looks
    like here.
    """

    run_id: str
    job: GpuJobEvidence
    training: TrainingSummaryEvidence | None
    capability: GpuCapabilityEvidence | None
    checkpoint: CheckpointObservation | None
    #: Present only for a run whose program asked. The probes and the resume were added
    #: after the first three runs, so absence here is a fact about when a run happened
    #: rather than about what it found -- which is why they are optional beside the rest.
    isolation: IsolationEvidence | None = None
    resume: ResumeEvidence | None = None
    #: Present only for a run whose training program resolved a published corpus and saved
    #: the config it did it with. The runs that predate the entry point trained on synthetic
    #: tokens, so absence here is a run that read no corpus rather than one nobody looked at.
    corpus: CorpusReadEvidence | None = None

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
                isolation=_optional(entry / ISOLATION_RECORD, IsolationEvidence),
                resume=_optional(entry / RESUME_RECORD, ResumeEvidence),
                corpus=_optional(entry / CORPUS_READ_RECORD, CorpusReadEvidence),
            )
        )
    return tuple(found)


def training_runs(root: Path = CAPTURE_ROOT) -> tuple[CapturedRun, ...]:
    """Every committed run that trained, oldest first, refused if there are none."""
    trained = tuple(run for run in captured_runs(root) if run.is_a_training_run)
    if not trained:
        raise MissingCaptureError("no committed run carries a training summary")
    return trained


def training_run(root: Path = CAPTURE_ROOT) -> CapturedRun:
    """The most recent run that trained, which is the one criteria are about.

    THIS USED TO REFUSE ANYTHING BUT EXACTLY ONE, and the reasoning was that a criterion
    saying "the GPU training run" should not silently start describing whichever sorted
    first. The reasoning was right and the rule was wrong: a second training run is the
    ordinary way this platform accumulates evidence, and refusing it would have meant
    deleting the first one to commit the second.

    The most recent, because these criteria are statements about what the platform does
    now. What the older runs establish is separate and is read through
    :func:`training_runs` -- the resume, in particular, is a claim about two runs and needs
    both.
    """
    return training_runs(root)[-1]


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


def role_captures(directory: Path = ROLE_CAPTURE_DIR) -> tuple[CommittedRoleCapture, ...]:
    """Every role this phase declares, and what the record committed for it establishes today.

    Read through Phase 1's reader rather than through a second one, so a Phase 4 role and a
    Phase 1 role produce the same verdicts and a reader meeting one has already met the
    other. What differs is the registry it walks and the directory it walks, which is what
    keeps a role this phase adds from failing an earlier phase's capture.

    Unlike the records above, this raises nothing for a capture that is absent, stale or
    drifted: each is a verdict on the capture, and collapsing them into an exception would
    lose the only part a reader can act on.
    """
    return read_committed_role_captures(
        REPO_ROOT,
        capture_dir=directory,
        role_templates=PHASE4_ROLE_TEMPLATES,
    )
