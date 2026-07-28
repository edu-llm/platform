"""Reading the Phase 3 captures somebody committed, and deciding what they are still worth.

``tools/capture_phase3_evidence.py`` reads the account and writes sanitized records; this
reads the ones a reviewer chose to commit and answers the only question a test or a proof
bundle should ask of one: does this still hold? Nothing here talks to AWS, so a criterion
may cite a test that calls it and the acceptance gate may execute that citation on a laptop
with no credentials. That is the same split ``phase1_capture`` draws, for the same reason.

**What a run's capture has to survive.** Five records describe one run -- the Batch job,
the lineage attestation, the admission execution, the session that started it and the log
stream -- and every one of them is a ``FreshEvidenceModel`` that stops loading after
``FRESHNESS_WINDOW``. On top of that they have to agree with each other, and the agreement
is the whole point: a Batch job captured under one run id and an attempt record naming a
different job would each be a true statement, and together they would establish nothing
about a run. :attr:`CommittedPhase3Run.problems` is where a disagreement is reported, and
it is empty only when every join holds.

**The lineage bodies do not expire and the records about them do.** A lineage object is
write-once and permanent; what expires is when somebody last went and looked at it. So the
bodies are committed as bytes with no freshness field, and the attestation that says what
S3 holds for each one carries the window. Re-capturing renews the attestation without the
run being repeated, which is the same distinction ``RUN_RECAPTURE_GUIDANCE`` draws in
Phase 1.

**A binding that does not load is a fact, not a missing file.** Three runs were written
before the ``"Result": null`` fix in the admission ASL and carry a whole admission payload
in the field where a fan-out size belongs. The store is write-once, so those objects will
never load, and the capture records them in the attestation and withholds the body. This
module therefore has to tell three states apart: a body that is committed and loads, a
body deliberately withheld because the object is known not to load, and a body that is
simply absent. Only the third is somebody forgetting, and only the first lets a run be
traced end to end.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final

from pydantic import ValidationError

from edullm_platform.evidence import (
    FRESHNESS_WINDOW,
    FreshEvidenceModel,
    evidence_load_reason_code,
)
from edullm_platform.phase1_capture import CAPTURE_SUFFIX, CaptureVerdict
from edullm_platform.phase1_evidence import OidcSessionEvidence
from edullm_platform.phase2_evidence import AdmissionExecution
from edullm_platform.phase3_evidence import (
    BatchJobEvidence,
    ComputeEnvironmentEvidence,
    LogStreamEvidence,
    RefusedRunEvidence,
    RunLineageAttestation,
)

__all__ = [
    "CAPTURE_SUFFIX",
    "COMPUTE_ENVIRONMENT_RECORD",
    "PHASE3_CAPTURE_DIR",
    "REFUSAL_RECORD",
    "REFUSED_RUN_RECORDS",
    "RUNS_SUBDIR",
    "RUN_RECAPTURE_GUIDANCE",
    "RUN_RECORDS",
    "TRACEABLE_ARTIFACTS",
    "CommittedPhase3Evidence",
    "CommittedPhase3Run",
    "Phase3EvidenceProblem",
    "problems_across",
    "read_committed_phase3_evidence",
    "reasons",
]

#: Where a Phase 3 capture lives once somebody has read it and decided to commit it.
#: Beside the account measurements rather than under ``proof/``: a bundle is generated and
#: these are not.
PHASE3_CAPTURE_DIR: Final = Path("fixtures") / "evidence" / "phase-3"
RUNS_SUBDIR: Final = "runs"
RECORDS_SUBDIR: Final = "records"
COMPUTE_ENVIRONMENT_RECORD: Final = "compute-environment"

#: The five records a submitted run produces, and the contract each is read through.
#: Driven from here rather than from the directory, so a record somebody deleted reads as
#: missing instead of vanishing from the answer.
RUN_RECORDS: Final[tuple[tuple[str, type[FreshEvidenceModel]], ...]] = (
    ("batch-job", BatchJobEvidence),
    ("lineage-attestation", RunLineageAttestation),
    ("admission-execution", AdmissionExecution),
    ("oidc-session", OidcSessionEvidence),
    ("log-stream", LogStreamEvidence),
)

#: What a run refused at admission produces instead. Three records rather than five,
#: because a run that was never submitted has no container and no stream -- and demanding
#: them would report a refusal that worked as a capture that failed.
REFUSED_RUN_RECORDS: Final[tuple[tuple[str, type[FreshEvidenceModel]], ...]] = (
    ("refusal", RefusedRunEvidence),
    ("lineage-attestation", RunLineageAttestation),
    ("admission-execution", AdmissionExecution),
)

#: The file whose presence says a run was refused rather than submitted. A refusal is not
#: an incomplete run capture and must not read as one.
REFUSAL_RECORD: Final = "refusal"

#: The eleven things a run id has to resolve to before the run is traceable end to end.
#: Named here rather than counted in a test, because the criterion is about this list and
#: a test that counted would go on passing after somebody removed one.
TRACEABLE_ARTIFACTS: Final = (
    "github_workflow_run",
    "oidc_session",
    "admission_execution",
    "intent",
    "decision",
    "binding",
    "event",
    "attempt",
    "result",
    "batch_job",
    "log_stream",
)

RUN_RECAPTURE_GUIDANCE: Final = (
    "Re-run tools/capture_phase3_evidence.py against the sandbox with --target run and "
    "--run-id for each run named here, and commit the sanitized records it writes into "
    f"{PHASE3_CAPTURE_DIR}/{RUNS_SUBDIR}/. The runs themselves do not need repeating: the "
    "Batch jobs, their log streams and every lineage object are still in the account, and "
    "the lineage store is write-once, so what expires is when somebody last looked rather "
    "than what they saw. If nobody is going to look again, delete the committed records "
    "and remove the citations resting on them from src/edullm_platform/phase3_criteria.py, "
    "which is a decision somebody takes in writing."
)


@dataclass(frozen=True)
class Phase3EvidenceProblem:
    """One reason a committed Phase 3 capture does not establish what it claims."""

    record: str
    reason: str
    detail: str


@dataclass(frozen=True)
class CommittedPhase3Run:
    """Everything one run left behind, as committed, and whether it still holds.

    Every record field is ``None`` when its record did not load, so a caller cannot read
    half a run as a whole one, and :attr:`problems` says which and why.
    """

    run_id: str
    job: BatchJobEvidence | None
    lineage: RunLineageAttestation | None
    execution: AdmissionExecution | None
    session: OidcSessionEvidence | None
    logs: LogStreamEvidence | None
    #: Present only for a run admission refused before submission. Its presence is what
    #: makes the missing Batch job and log stream expected rather than a broken capture.
    refusal: RefusedRunEvidence | None = None
    #: The lineage bodies that were committed, keyed by record kind. ``events`` holds
    #: every event object; the other kinds hold at most one.
    bodies: Mapping[str, tuple[Mapping[str, Any], ...]] = field(default_factory=dict)
    problems: tuple[Phase3EvidenceProblem, ...] = ()

    @property
    def holds(self) -> bool:
        return not self.problems

    @property
    def was_refused(self) -> bool:
        return self.refusal is not None

    def body(self, kind: str) -> Mapping[str, Any] | None:
        """The single committed body of one kind, or ``None`` when there is not exactly one."""
        found = self.bodies.get(kind, ())
        return found[0] if len(found) == 1 else None

    @property
    def outcome(self) -> str | None:
        """How the run ended: a terminal state, ``refused``, or nothing yet.

        ``refused`` is its own word rather than folded into ``failed``. A run that was
        refused never started, so it cost nothing and produced no container; a run that
        failed spent money and left output. Reporting them alike would make the two
        criteria that rest on the difference cite the same evidence.
        """
        if self.refusal is not None:
            return "refused"
        result = self.body("result")
        return None if result is None else str(result.get("outcome"))

    @property
    def artifacts(self) -> Mapping[str, bool]:
        """Which of the eleven traceable artifacts this run id actually resolves to.

        ``binding`` is true only when the object loads as a binding. An attested,
        versioned, permanently malformed record is not a link in a chain a reader can
        follow, and counting it would make the three corrupt runs look traceable.
        """
        intent = self.body("intent")
        return {
            "github_workflow_run": bool(
                intent is not None and (intent.get("workflow_run") or {}).get("run_id")
            ),
            "oidc_session": self.session is not None,
            "admission_execution": self.execution is not None,
            "intent": intent is not None,
            "decision": self.body("decision") is not None,
            "binding": self.body("binding") is not None,
            "event": bool(self.bodies.get("events")),
            "attempt": self.body("attempt") is not None,
            "result": self.body("result") is not None,
            "batch_job": self.job is not None,
            "log_stream": self.logs is not None,
        }

    @property
    def traceable(self) -> bool:
        return self.holds and all(self.artifacts.values())

    @property
    def unresolved_artifacts(self) -> tuple[str, ...]:
        return tuple(name for name in TRACEABLE_ARTIFACTS if not self.artifacts[name])


@dataclass(frozen=True)
class CommittedPhase3Evidence:
    """Every committed run, plus the compute environment they ran on."""

    runs: tuple[CommittedPhase3Run, ...]
    compute_environment: ComputeEnvironmentEvidence | None
    problems: tuple[Phase3EvidenceProblem, ...]

    @property
    def holds(self) -> bool:
        return not self.problems and all(run.holds for run in self.runs)

    def run(self, run_id: str) -> CommittedPhase3Run | None:
        for captured in self.runs:
            if captured.run_id == run_id:
                return captured
        return None

    def runs_with_outcome(self, outcome: str) -> tuple[CommittedPhase3Run, ...]:
        return tuple(captured for captured in self.runs if captured.outcome == outcome)


def _load_record[T: FreshEvidenceModel](
    path: Path, contract: type[T], *, run_id: str
) -> tuple[T | None, Phase3EvidenceProblem | None]:
    """One committed file, whatever state it is in. Never raises for its contents.

    Generic in the contract rather than returning the base, so a caller gets back the type
    it asked for. Declared as ``FreshEvidenceModel | None`` this compiled and lied: every
    caller had to know the real type anyway, and the one that passes the result straight
    into a typed field was handing over a base-class value nothing checked.
    """
    name = path.name.removesuffix(CAPTURE_SUFFIX)
    if not path.is_file():
        return None, Phase3EvidenceProblem(
            record=f"{run_id}/{name}",
            reason=CaptureVerdict.ABSENT.value,
            detail=(
                f"No {name} record is committed for {run_id}, so nothing here says what "
                f"that part of the run produced. {RUN_RECAPTURE_GUIDANCE}"
            ),
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, Phase3EvidenceProblem(
            record=f"{run_id}/{name}",
            reason=CaptureVerdict.INVALID.value,
            detail=f"{path.name} is not readable JSON, so it records nothing.",
        )
    try:
        return contract.model_validate(payload), None
    except ValidationError as error:
        reason = evidence_load_reason_code(error)
        stale = reason == CaptureVerdict.STALE.value
        return None, Phase3EvidenceProblem(
            record=f"{run_id}/{name}",
            reason=reason,
            detail=(
                f"The committed {name} record for {run_id} is more than "
                f"{FRESHNESS_WINDOW.days} days old and no longer loads, so it establishes "
                f"nothing about the run. {RUN_RECAPTURE_GUIDANCE}"
                if stale
                else f"{name} does not load as a {contract.__name__}: {reason}."
            ),
        )


def _read_bodies(directory: Path) -> dict[str, tuple[Mapping[str, Any], ...]]:
    """Every committed lineage body, grouped by the record kind its key names."""
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    if not directory.is_dir():
        return {}
    for path in sorted(directory.rglob("*.json")):
        kind = path.relative_to(directory).parts[0]
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict):
            grouped.setdefault(kind, []).append(payload)
    return {kind: tuple(bodies) for kind, bodies in grouped.items()}


def _joins(run: CommittedPhase3Run) -> tuple[Phase3EvidenceProblem, ...]:
    """Whether these records are all about the same run, the same job and the same attempt.

    Nothing here re-checks a rule a contract already enforces. What is checked is what no
    single record can see: that they agree with each other.
    """
    problems: list[Phase3EvidenceProblem] = []

    def complain(record: str, reason: str, detail: str) -> None:
        problems.append(Phase3EvidenceProblem(record=record, reason=reason, detail=detail))

    if run.job is not None and run.job.run_id != run.run_id:
        complain(
            "batch-job",
            "record_describes_another_run",
            f"The committed Batch job is filed under {run.run_id} and names "
            f"{run.job.run_id}, so it says nothing about this run.",
        )
    if run.job is not None and not run.job.joins_to_its_run:
        complain(
            "batch-job",
            "job_name_is_not_the_run_id",
            f"Batch holds {run.job.batch_job_name} as the job name where the run id is "
            f"{run.job.run_id}. The run id being the job name is the third join; without "
            "it a job and its lineage records cannot be matched without a lookup table.",
        )
    if run.lineage is not None and run.lineage.run_id != run.run_id:
        complain(
            "lineage-attestation",
            "record_describes_another_run",
            f"The committed attestation is filed under {run.run_id} and names "
            f"{run.lineage.run_id}.",
        )
    if run.execution is not None and run.execution.name != run.run_id:
        complain(
            "admission-execution",
            "record_describes_another_run",
            f"The committed execution is named {run.execution.name} rather than "
            f"{run.run_id}. The execution name is the run id, which is what makes a "
            "second submission under the same id refusable.",
        )
    if run.logs is not None and run.job is not None:
        if run.logs.log_stream_name != run.job.log_stream_name:
            complain(
                "log-stream",
                "log_stream_is_not_the_jobs_stream",
                "The committed log stream is not the stream the Batch job recorded, so "
                "the lines in it were printed by some other container.",
            )
        if not run.logs.lines:
            complain(
                "log-stream",
                "log_stream_carried_no_output",
                "The committed stream resolves and holds no lines, so it does not show "
                "that stdout reached the recorded stream.",
            )

    if run.refusal is not None:
        if run.refusal.run_id != run.run_id:
            complain(
                "refusal",
                "record_describes_another_run",
                f"The committed refusal names {run.refusal.run_id} and is filed under "
                f"{run.run_id}.",
            )
        if run.refusal.matching_batch_job_ids:
            complain(
                "refusal",
                "refused_run_started_a_job",
                "Admission refused this run and a Batch job exists under its run id. The "
                "refusal did not prevent a submission, which is the failure the check "
                "exists to find rather than a defect in the capture.",
            )
        if not run.refusal.searched_every_status:
            complain(
                "refusal",
                "absence_established_nowhere",
                "The refusal record does not name every Batch status as searched, so the "
                "absence of a job is not an absence anybody established.",
            )
        started = sorted(set(run.bodies) - {"intent", "decision"})
        if started:
            complain(
                "refusal",
                "refused_run_wrote_execution_records",
                f"Admission refused this run and it wrote {', '.join(started)} anyway. A "
                "refusal happens before submission, so nothing past the decision should "
                "exist.",
            )

    attempt = run.body("attempt")
    result = run.body("result")
    binding = run.body("binding")
    if (
        attempt is not None
        and run.job is not None
        and str(attempt.get("scheduler_job_id")) != run.job.batch_job_id
    ):
        complain(
            "attempt",
            "attempt_names_another_job",
            "The committed attempt names a different Batch job from the one captured "
            "for this run, so the result cannot be traced to the container that ran.",
        )
    if (
        result is not None
        and attempt is not None
        and str(result.get("attempt_id")) != str(attempt.get("attempt_id"))
    ):
        complain(
            "result",
            "result_names_another_attempt",
            "The committed result names a different attempt from the one committed "
            "for this run.",
        )
    if (
        binding is not None
        and run.job is not None
        and str(binding.get("batch_job_id")) != run.job.batch_job_id
    ):
        complain(
            "binding",
            "binding_names_another_job",
            "The binding the platform wrote names a different Batch job from the one "
            "the service describes, so the record of what was launched disagrees with "
            "what ran.",
        )
    for kind in ("intent", "decision", "binding", "attempt", "result"):
        bodies = run.bodies.get(kind, ())
        if len(bodies) > 1:
            complain(
                kind,
                "more_than_one_record_of_its_kind",
                f"{len(bodies)} {kind} records are committed for {run.run_id}, and a run "
                "writes exactly one.",
            )
    for kind, bodies in run.bodies.items():
        for body in bodies:
            if str(body.get("run_id")) != run.run_id:
                complain(
                    kind,
                    "record_describes_another_run",
                    f"A committed {kind} body names run {body.get('run_id')} and is filed "
                    f"under {run.run_id}.",
                )
                break
    return tuple(problems)


def _read_run(directory: Path) -> CommittedPhase3Run:
    """One run's committed capture, whatever state it is in.

    Which record set is expected is decided by whether a refusal record is committed, not
    by what happens to be on disk. Inferring it from the absence of a Batch job would mean
    a submitted run whose job capture somebody deleted would silently reclassify itself as
    a refusal -- turning a missing record into a clean bill of health.
    """
    run_id = directory.name
    refused = (directory / f"{REFUSAL_RECORD}{CAPTURE_SUFFIX}").is_file()
    expected = REFUSED_RUN_RECORDS if refused else RUN_RECORDS
    loaded: dict[str, Any] = {}
    problems: list[Phase3EvidenceProblem] = []
    for name, contract in expected:
        record, problem = _load_record(
            directory / f"{name}{CAPTURE_SUFFIX}", contract, run_id=run_id
        )
        loaded[name] = record
        if problem is not None:
            problems.append(problem)

    lineage = loaded["lineage-attestation"]
    bodies = _read_bodies(directory / RECORDS_SUBDIR)
    if isinstance(lineage, RunLineageAttestation):
        # Every attested object either has its body committed or is one the attestation
        # says does not load. A body missing for any other reason is somebody removing
        # evidence, and it has to read differently from the capture deliberately
        # withholding a record that will never load.
        # ``attested`` rather than ``record``: the loop above binds ``record`` to a loaded
        # contract, and reusing the name here made every attribute on it a type error while
        # the code itself was correct.
        for attested in lineage.objects:
            if attested.loads_as_contract and not bodies.get(attested.record_kind):
                problems.append(
                    Phase3EvidenceProblem(
                        record=attested.record_kind,
                        reason=CaptureVerdict.ABSENT.value,
                        detail=(
                            f"{attested.key} is attested in the committed capture of "
                            f"{run_id} and loads, but its body is not committed beside "
                            f"it. {RUN_RECAPTURE_GUIDANCE}"
                        ),
                    )
                )
    run = CommittedPhase3Run(
        run_id=run_id,
        job=loaded.get("batch-job"),
        lineage=lineage,
        execution=loaded.get("admission-execution"),
        session=loaded.get("oidc-session"),
        logs=loaded.get("log-stream"),
        refusal=loaded.get(REFUSAL_RECORD),
        bodies=bodies,
        problems=tuple(problems),
    )
    return replace(run, problems=(*run.problems, *_joins(run)))


def read_committed_phase3_evidence(
    repo_root: Path,
    *,
    directory: Path | None = None,
) -> CommittedPhase3Evidence:
    """The committed record of every captured Phase 3 run, and what it is worth today."""
    root = repo_root / PHASE3_CAPTURE_DIR if directory is None else directory
    problems: list[Phase3EvidenceProblem] = []

    runs_root = root / RUNS_SUBDIR
    runs = tuple(
        _read_run(child)
        for child in sorted(runs_root.iterdir())
        if child.is_dir()
    ) if runs_root.is_dir() else ()
    if not runs:
        problems.append(
            Phase3EvidenceProblem(
                record=RUNS_SUBDIR,
                reason=CaptureVerdict.ABSENT.value,
                detail=(
                    f"No run is committed under {PHASE3_CAPTURE_DIR}/{RUNS_SUBDIR}/, so "
                    "nothing here says that this platform has ever run a container. "
                    f"{RUN_RECAPTURE_GUIDANCE}"
                ),
            )
        )

    environment, problem = _load_record(
        root / f"{COMPUTE_ENVIRONMENT_RECORD}{CAPTURE_SUFFIX}",
        ComputeEnvironmentEvidence,
        run_id="compute-environment",
    )
    if problem is not None:
        problems.append(problem)

    return CommittedPhase3Evidence(
        runs=runs,
        compute_environment=environment,
        problems=tuple(problems),
    )


def problems_across(evidence: CommittedPhase3Evidence) -> tuple[Phase3EvidenceProblem, ...]:
    """Every problem, the run-level ones included, for a caller that wants one list."""
    collected: list[Phase3EvidenceProblem] = list(evidence.problems)
    for run in evidence.runs:
        collected.extend(run.problems)
    return tuple(collected)


def reasons(problems: Sequence[Phase3EvidenceProblem]) -> frozenset[str]:
    return frozenset(problem.reason for problem in problems)
