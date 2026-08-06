"""Reading the captures somebody committed, and deciding what they are still worth.

``tools/capture_phase1_evidence.py`` reads the account and writes sanitized records; this
reads the records a reviewer chose to commit and answers the only question a test or a
proof bundle should ask of one: does this still hold? Nothing here talks to AWS, so a
criterion may cite a test that calls it and the acceptance gate may execute that citation
on a laptop with no credentials.

**A capture holds while three things are true at once.** It must load — every record in
``edullm_platform.phase1_evidence`` is a ``FreshEvidenceModel`` and refuses to load once
it is older than ``FRESHNESS_WINDOW``. It must describe a role a committed template
declares, because a record nothing can be compared against establishes nothing. And the
comparison must find no drift. Fail any of those and the capture does not hold, which is
:attr:`CommittedRoleCapture.holds`, and the verdict says which.

**Nothing renews this, and nothing should.** Thirty days after the capture the record
stops loading, every citation resting on it fails, and the criteria that rest on those
citations are gaps again with the gate red. That is not a defect to work around: the
window exists because a role deployed by hand can be widened by hand, and the only thing
that establishes it has not been is somebody going and looking again. The two honest
responses are in :data:`RECAPTURE_GUIDANCE`, and neither of them is editing the window.

**Absent is not the same as stale, and neither is the same as invalid.** A missing record
means nobody has looked; a stale one means somebody looked too long ago; an invalid one
means what they wrote down is not a role capture. Collapsing the three would lose the
only part a reader can act on, so each is its own verdict and each carries its own text.
Those three are :class:`~edullm_platform.evidence.CaptureLoadVerdict` and are not Phase
1's: every phase that reads a committed record asks them first, and two readers in this
module ask them of things that are not roles at all.

**Drift somebody has written down is not the same as drift nobody has.** The stacks that
hold these roles are applied from a laptop, so a template amendment lands before the deploy
that realises it and the account is genuinely behind the template in between.
:mod:`edullm_platform.pending_amendments` is where that difference is recorded, and a
capture reporting exactly the recorded findings gets its own verdict,
:attr:`CaptureVerdict.PENDING_DEPLOY`, rather than the one a role widened in the console
would get. It still does not hold: the criteria resting on it are still not certified and
the proof generator still refuses to build on it. What the verdict buys is that a reader
downstream can tell "waiting on a deploy" from "expired" without re-deriving it, which is
what one consumer got wrong by treating every capture that stopped holding as the former.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from edullm_platform.evidence import (
    CAPTURE_SUFFIX,
    FRESHNESS_WINDOW,
    CaptureLoadVerdict,
    FreshEvidenceModel,
    evidence_load_reason_code,
)
from edullm_platform.pending_amendments import PendingAmendment, pending_for
from edullm_platform.phase1_evidence import (
    DenialEvidence,
    DeployedRoleEvidence,
    EcrImageEvidence,
    EcrRepositoryEvidence,
    ImageScanEvidence,
    ImmutableTagRefusalEvidence,
    OidcSessionEvidence,
)
from edullm_platform.publisher_denials import PUBLISHER_DENIED_ACTIONS
from edullm_platform.role_drift import (
    COMMITTED_ROLE_TEMPLATES,
    PUBLISHER_ROLE_NAME,
    RoleDriftReport,
    compare_role_to_template,
    load_template_roles,
)

__all__ = [
    "CAPTURE_PARTITION",
    "CAPTURE_REGION",
    "RECAPTURE_GUIDANCE",
    "ROLE_CAPTURE_DIR",
    "RUN_CAPTURE_DIR",
    "RUN_RECAPTURE_GUIDANCE",
    "CaptureVerdict",
    "CommittedRoleCapture",
    "CommittedRunEvidence",
    "RunEvidenceProblem",
    "captures_pending_a_deploy",
    "captures_that_do_not_hold",
    "only_a_pending_deploy_stands_in_the_way",
    "read_committed_role_captures",
    "read_committed_run_evidence",
]

#: Where a capture lives once somebody has read it and decided to commit it. Beside the
#: other committed evidence, because this is a reading of the account rather than a report.
ROLE_CAPTURE_DIR: Final = Path("fixtures") / "evidence" / "phase-1" / "roles"

#: The partition and region the comparison is allowed to fold, which are the ones the
#: account is in. Named here rather than defaulted inside ``role_drift``, which requires
#: them of every caller so that nobody folds a region by accident.
CAPTURE_PARTITION: Final = "aws"
CAPTURE_REGION: Final = "us-east-1"

RECAPTURE_GUIDANCE: Final = (
    "Re-run tools/capture_phase1_evidence.py against the sandbox and commit the sanitized "
    f"role records it writes into {ROLE_CAPTURE_DIR}/. If nobody is going to look at the "
    "account again, delete the committed records and remove the citations resting on them "
    "from src/edullm_platform/phase1_criteria.py, which is a decision somebody takes in "
    "writing. Leaving an expired record where it is, is the one option that reads as proof "
    "and is not."
)


class CaptureVerdict(StrEnum):
    """What a role capture that loaded says about the role, once compared to its template.

    Every member here is about that comparison, which is why three of the four spell
    ``role_`` in their value. A capture that did not load says nothing about a template
    and gets a :class:`~edullm_platform.evidence.CaptureLoadVerdict` instead; the two
    together are what :attr:`CommittedRoleCapture.verdict` may hold.
    """

    #: Loaded, inside its window, and identical to the template that declares the role.
    OK = "ok"
    #: A record loaded and disagrees with the template that declares the role.
    DRIFTED = "role_drift"
    #: A record loaded and disagrees with its template in exactly the way a recorded
    #: pending amendment says it will, because the template is committed and the deploy
    #: that realises it has not been run. Distinct from ``DRIFTED`` and equally not ``OK``:
    #: the difference is expected, and the capture still establishes nothing about the
    #: template as it now stands.
    PENDING_DEPLOY = "role_drift_pending_deploy"
    #: A record loaded and names a role no committed template declares.
    UNDECLARED = "role_has_no_committed_template"


@dataclass(frozen=True)
class CommittedRoleCapture:
    """One role, and what the record committed for it establishes today.

    ``evidence`` and ``report`` are present only when there was something to record: a
    stale record does not load, so there is no role to hand a reader, and a record for an
    undeclared role has nothing to be compared against. Both are ``None`` in those cases
    rather than partly filled in, so a caller cannot read half a capture as a whole one.
    """

    role_name: str
    template_path: str | None
    capture_path: str | None
    verdict: CaptureVerdict | CaptureLoadVerdict
    detail: str
    evidence: DeployedRoleEvidence | None = None
    report: RoleDriftReport | None = None

    @property
    def holds(self) -> bool:
        return self.verdict is CaptureVerdict.OK

    @property
    def expires_at(self) -> datetime | None:
        """When this record stops loading, or ``None`` when it already has."""
        if self.evidence is None:
            return None
        return self.evidence.observed_at + FRESHNESS_WINDOW


def _relative(path: Path, repo_root: Path) -> str:
    return str(path.relative_to(repo_root)) if path.is_relative_to(repo_root) else path.name


def _stale_detail(role_name: str) -> str:
    return (
        f"The committed capture of {role_name} is more than "
        f"{FRESHNESS_WINDOW.days} days old and no longer loads, so it establishes nothing "
        f"about the account. {RECAPTURE_GUIDANCE}"
    )


def _drift_detail(report: RoleDriftReport) -> str:
    return (
        f"The deployed {report.role_name} no longer matches {report.template_path}: "
        + "; ".join(
            f"{finding.direction.value} at {finding.element} — {finding.detail}"
            for finding in report.findings
        )
    )


def _pending_detail(report: RoleDriftReport, pending: PendingAmendment) -> str:
    # `describe_clearing` rather than the record's own prose, because the stack it names is
    # derived from the role and the template rather than typed. A reader who acts on this
    # line is applying the stack it gives, so the line has to be the derived one.
    return (
        f"{_drift_detail(report)}. That is exactly the difference recorded as pending for "
        f"{report.role_name}, so it is expected rather than unexplained — and it is still "
        f"not agreement. Why: {pending.reason} Cleared by: {pending.describe_clearing()}"
    )


def _verdict_and_detail(
    report: RoleDriftReport,
    *,
    role_name: str,
    relative_path: str,
    observed_on: str,
) -> tuple[CaptureVerdict, str]:
    """Which of the three comparison outcomes this is, and what to tell a reader.

    Three rather than two. Agreement, a difference somebody recorded and is waiting on,
    and a difference nobody has explained are distinct facts, and only the first is
    ``OK``. Folding the middle one into either neighbour loses something: into ``OK`` and
    a bundle would certify criteria resting on a role the account does not hold; into
    ``DRIFTED`` and a reader cannot tell an undeployed amendment from a console edit.
    """
    if report.matches:
        return CaptureVerdict.OK, (
            f"The deployed {role_name} matches {relative_path} as observed on {observed_on}."
        )
    pending = pending_for(role_name)
    if pending is not None and pending.explains(report.findings):
        return CaptureVerdict.PENDING_DEPLOY, _pending_detail(report, pending)
    return CaptureVerdict.DRIFTED, _drift_detail(report)


def _read_one(
    path: Path,
    *,
    repo_root: Path,
    templates: dict[str, str],
    partition: str,
    region: str,
) -> CommittedRoleCapture:
    """One committed file, whatever state it is in. Never raises for its contents."""
    capture_path = _relative(path, repo_root)
    named = path.name[: -len(CAPTURE_SUFFIX)]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return CommittedRoleCapture(
            role_name=named,
            template_path=templates.get(named),
            capture_path=capture_path,
            verdict=CaptureLoadVerdict.INVALID,
            detail=f"{capture_path} is not readable JSON, so it records nothing.",
        )
    try:
        evidence = DeployedRoleEvidence.model_validate(payload)
    except ValidationError as error:
        # The filename is all there is to go on: a record that failed its contract may
        # not have a role name, and one that has one may have any string in the field.
        reason = evidence_load_reason_code(error)
        stale = reason == CaptureLoadVerdict.STALE.value
        return CommittedRoleCapture(
            role_name=named,
            template_path=templates.get(named),
            capture_path=capture_path,
            verdict=CaptureLoadVerdict.STALE if stale else CaptureLoadVerdict.INVALID,
            detail=(
                _stale_detail(named)
                if stale
                else f"{capture_path} does not load as a captured role: {reason}."
            ),
        )
    relative_path = templates.get(evidence.role_name)
    if relative_path is None:
        return CommittedRoleCapture(
            role_name=evidence.role_name,
            template_path=None,
            capture_path=capture_path,
            verdict=CaptureVerdict.UNDECLARED,
            detail=(
                f"{capture_path} describes {evidence.role_name}, which no committed template "
                "declares, so there is nothing to compare it against."
            ),
            evidence=evidence,
        )
    template = next(
        role
        for role in load_template_roles(repo_root / relative_path)
        if role.role_name == evidence.role_name
    )
    report = compare_role_to_template(
        evidence,
        template,
        template_path=relative_path,
        partition=partition,
        region=region,
    )
    verdict, detail = _verdict_and_detail(
        report,
        role_name=evidence.role_name,
        relative_path=relative_path,
        observed_on=evidence.observed_at.date().isoformat(),
    )
    return CommittedRoleCapture(
        role_name=evidence.role_name,
        template_path=relative_path,
        capture_path=capture_path,
        verdict=verdict,
        detail=detail,
        evidence=evidence,
        report=report,
    )


def read_committed_role_captures(
    repo_root: Path,
    *,
    capture_dir: Path | None = None,
    partition: str = CAPTURE_PARTITION,
    region: str = CAPTURE_REGION,
    role_templates: Sequence[tuple[str, str]] | None = None,
) -> tuple[CommittedRoleCapture, ...]:
    """Every role a template declares, plus any captured role none of them does.

    Driven by the template list rather than by the directory, so a capture somebody
    deleted is reported as a role nobody has looked at instead of vanishing from the
    answer. A record naming a role no template declares is reported too, for the same
    reason in the other direction.

    A template this module cannot project raises rather than producing a verdict: that is
    a defect in the repository rather than a fact about the account, and reporting it as a
    capture that does not hold would point the reader at the wrong half.

    ``role_templates`` defaults to Phase 1's registry and is a parameter so that Phase 3
    can compare its own four roles through the same machinery. The registries stay
    separate -- a Phase 3 role drifting must not fail a Phase 1 capture -- and what is
    shared is the comparison rather than the list.
    """
    templates = dict(
        COMMITTED_ROLE_TEMPLATES if role_templates is None else role_templates
    )
    directory = repo_root / ROLE_CAPTURE_DIR if capture_dir is None else capture_dir
    found: list[CommittedRoleCapture] = []
    if directory.is_dir():
        found = [
            _read_one(
                path,
                repo_root=repo_root,
                templates=templates,
                partition=partition,
                region=region,
            )
            for path in sorted(directory.glob(f"*{CAPTURE_SUFFIX}"))
        ]
    captured = {capture.role_name for capture in found}
    absent = [
        CommittedRoleCapture(
            role_name=role_name,
            template_path=relative_path,
            capture_path=None,
            verdict=CaptureLoadVerdict.ABSENT,
            detail=(
                f"No capture of {role_name} is committed under {ROLE_CAPTURE_DIR}/, so "
                f"{relative_path} is a claim about the account that nothing has checked. "
                f"{RECAPTURE_GUIDANCE}"
            ),
        )
        for role_name, relative_path in templates.items()
        if role_name not in captured
    ]
    return tuple(sorted([*found, *absent], key=lambda capture: capture.role_name))


def captures_that_do_not_hold(
    captures: Sequence[CommittedRoleCapture],
) -> tuple[CommittedRoleCapture, ...]:
    return tuple(capture for capture in captures if not capture.holds)


def captures_pending_a_deploy(
    captures: Sequence[CommittedRoleCapture],
) -> tuple[CommittedRoleCapture, ...]:
    """The captures whose only difference from their template is one somebody recorded."""
    return tuple(
        capture for capture in captures if capture.verdict is CaptureVerdict.PENDING_DEPLOY
    )


def only_a_pending_deploy_stands_in_the_way(
    captures: Sequence[CommittedRoleCapture],
) -> tuple[CommittedRoleCapture, ...]:
    """The pending captures, or nothing at all if anything else has also stopped holding.

    The distinction this draws is the reason the verdict exists. A caller that treats
    "waiting on a laptop deploy" as a reason to stand down -- skipping a case, softening a
    message -- must not do so while some other capture has expired, been edited into
    something that does not load, or drifted for a reason nobody wrote down. Returning
    nothing in that case makes the caller take the ordinary path, which is the loud one.

    All-or-nothing rather than per-role, because the callers ask a whole-tree question:
    can a bundle be built, is this refusal the expected one. One expired capture is enough
    for the answer to be no for a reason nobody has recorded.
    """
    broken = captures_that_do_not_hold(captures)
    pending = captures_pending_a_deploy(captures)
    return pending if len(pending) == len(broken) else ()


# --------------------------------------------------------------------------------------
# What one publish run left behind
# --------------------------------------------------------------------------------------

#: Where the records of one completed publish run live once somebody has read them and
#: decided to commit them. Beside the role captures for the same reason: a report is
#: generated and these are not.
RUN_CAPTURE_DIR: Final = Path("fixtures") / "evidence" / "phase-1" / "run"
DENIALS_SUBDIR: Final = "denials"

#: The five records one run produces beside its denials, and the contract each is read
#: through. Driven from here rather than from the directory, so a record somebody deleted
#: reads as missing instead of vanishing from the answer.
RUN_RECORDS: Final[tuple[tuple[str, type[FreshEvidenceModel]], ...]] = (
    ("ecr-image", EcrImageEvidence),
    ("image-scan", ImageScanEvidence),
    ("publisher-session", OidcSessionEvidence),
    ("immutable-tag-refusal", ImmutableTagRefusalEvidence),
    ("ecr-repository", EcrRepositoryEvidence),
)

RUN_RECAPTURE_GUIDANCE: Final = (
    "Re-run tools/capture_phase1_evidence.py against the sandbox with --target image "
    "--target scan --target session --target tag-refusal --target repository and "
    f"--target denials, and commit the sanitized records it writes into {RUN_CAPTURE_DIR}/. "
    "The run itself does not need repeating: the image, its scan, the session that pushed "
    "it and the refusals it met are all still in the account and in CloudTrail, so what "
    "expires is when somebody last looked rather than what they saw. If nobody is going "
    "to look again, delete the committed records and remove the citations resting on them "
    "from src/edullm_platform/phase1_criteria.py, which is a decision somebody takes in "
    "writing."
)


@dataclass(frozen=True)
class RunEvidenceProblem:
    """One reason the committed record of a run does not establish what it claims."""

    record: str
    reason: str
    detail: str


@dataclass(frozen=True)
class CommittedRunEvidence:
    """Everything one publish run left behind, as committed, and whether it holds.

    A role capture is checked against a template. These records have no template, so what
    is checked instead is that they are about the same image: a scan filed under another
    digest, a refusal on another tag, or a session held by another role would each read as
    a statement about this run and be a statement about something else. Those joins are
    the whole of what :attr:`holds` means, together with the two things every committed
    capture needs — that the record is there and that somebody looked recently enough.

    Every field is ``None`` when its record did not load, so a caller cannot read half a
    run as a whole one, and :attr:`problems` says which and why.
    """

    image: EcrImageEvidence | None
    scan: ImageScanEvidence | None
    session: OidcSessionEvidence | None
    refusal: ImmutableTagRefusalEvidence | None
    repository: EcrRepositoryEvidence | None
    denials: tuple[DenialEvidence, ...]
    problems: tuple[RunEvidenceProblem, ...]

    @property
    def holds(self) -> bool:
        return not self.problems

    @property
    def denied_actions(self) -> tuple[str, ...]:
        return tuple(denial.attempted_action for denial in self.denials)


def _load_run_record(
    path: Path,
    contract: type[FreshEvidenceModel],
) -> tuple[FreshEvidenceModel | None, RunEvidenceProblem | None]:
    """One committed file, whatever state it is in. Never raises for its contents."""
    name = path.name.removesuffix(CAPTURE_SUFFIX)
    if not path.is_file():
        return None, RunEvidenceProblem(
            record=name,
            reason=CaptureLoadVerdict.ABSENT.value,
            detail=(
                f"No {name} record is committed under {RUN_CAPTURE_DIR}/, so nothing here "
                f"says what the run produced. {RUN_RECAPTURE_GUIDANCE}"
            ),
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, RunEvidenceProblem(
            record=name,
            reason=CaptureLoadVerdict.INVALID.value,
            detail=f"{name} is not readable JSON, so it records nothing.",
        )
    try:
        return contract.model_validate(payload), None
    except ValidationError as error:
        reason = evidence_load_reason_code(error)
        stale = reason == CaptureLoadVerdict.STALE.value
        return None, RunEvidenceProblem(
            record=name,
            reason=reason,
            detail=(
                f"The committed {name} record is more than {FRESHNESS_WINDOW.days} days old "
                f"and no longer loads, so it establishes nothing about the run. "
                f"{RUN_RECAPTURE_GUIDANCE}"
                if stale
                else f"{name} does not load as a {contract.__name__}: {reason}."
            ),
        )


def _joins(
    image: EcrImageEvidence | None,
    scan: ImageScanEvidence | None,
    session: OidcSessionEvidence | None,
    refusal: ImmutableTagRefusalEvidence | None,
    repository: EcrRepositoryEvidence | None,
    denials: Sequence[DenialEvidence],
) -> tuple[RunEvidenceProblem, ...]:
    """Whether these records are all about the same image, the same role and the same run.

    Nothing here re-checks a rule a contract already enforces. What is checked is what no
    single record can see: that the five of them agree with each other.
    """
    problems: list[RunEvidenceProblem] = []
    if image is None:
        # Every join below is against the image, so with no image there is nothing to
        # check rather than five further failures saying the same thing.
        return ()
    if scan is not None and scan.image_digest != image.image_digest:
        problems.append(
            RunEvidenceProblem(
                record="image-scan",
                reason="record_describes_another_image",
                detail=(
                    "The committed scan is of a different digest from the committed image, "
                    "so it says nothing about what this run published."
                ),
            )
        )
    if refusal is not None and (
        refusal.image_digest != image.image_digest or refusal.image_tag != image.image_tag
    ):
        problems.append(
            RunEvidenceProblem(
                record="immutable-tag-refusal",
                reason="record_describes_another_image",
                detail=(
                    "The committed refusal is about a different tag or resolves to a "
                    "different digest from the committed image, so it does not say that "
                    "this image survived a second push."
                ),
            )
        )
    if repository is not None and repository.repository_name != image.repository_name:
        problems.append(
            RunEvidenceProblem(
                record="ecr-repository",
                reason="record_describes_another_repository",
                detail=(
                    "The committed repository record is of a different repository from the "
                    "one the image was published to."
                ),
            )
        )
    if session is not None and session.role_name != PUBLISHER_ROLE_NAME:
        problems.append(
            RunEvidenceProblem(
                record="publisher-session",
                reason="session_is_not_the_publisher_role",
                detail=(
                    f"The committed session was held by {session.role_name} rather than by "
                    f"{PUBLISHER_ROLE_NAME}, so it is not the session this phase is about."
                ),
            )
        )
    attempted = tuple(denial.attempted_action for denial in denials)
    if attempted != PUBLISHER_DENIED_ACTIONS:
        problems.append(
            RunEvidenceProblem(
                record=DENIALS_SUBDIR,
                reason="denial_matrix_incomplete",
                detail=(
                    "The committed denials are not one record per matrix action, in matrix "
                    f"order. Committed: {', '.join(attempted) or 'nothing'}. A run that "
                    "refused some of the actions proved the criterion for those actions, "
                    "and a partial set read later would look like a run that refused them "
                    f"all. {RUN_RECAPTURE_GUIDANCE}"
                ),
            )
        )
    return tuple(problems)


def read_committed_run_evidence(
    repo_root: Path,
    *,
    directory: Path | None = None,
) -> CommittedRunEvidence:
    """The committed record of one publish run, and what it is still worth today."""
    root = repo_root / RUN_CAPTURE_DIR if directory is None else directory
    loaded: dict[str, FreshEvidenceModel | None] = {}
    problems: list[RunEvidenceProblem] = []
    for name, contract in RUN_RECORDS:
        record, problem = _load_run_record(root / f"{name}{CAPTURE_SUFFIX}", contract)
        loaded[name] = record
        if problem is not None:
            problems.append(problem)

    denials: list[DenialEvidence] = []
    denials_dir = root / DENIALS_SUBDIR
    # Ordered by the matrix rather than by filename, so a record for an action the matrix
    # does not name is absent from this list and reported by the ordering check below.
    for action in PUBLISHER_DENIED_ACTIONS:
        path = denials_dir / f"{action.replace(':', '-')}{CAPTURE_SUFFIX}"
        record, problem = _load_run_record(path, DenialEvidence)
        if problem is not None:
            problems.append(problem)
        elif isinstance(record, DenialEvidence):
            denials.append(record)

    image = loaded["ecr-image"]
    scan = loaded["image-scan"]
    session = loaded["publisher-session"]
    refusal = loaded["immutable-tag-refusal"]
    repository = loaded["ecr-repository"]
    assert image is None or isinstance(image, EcrImageEvidence)
    assert scan is None or isinstance(scan, ImageScanEvidence)
    assert session is None or isinstance(session, OidcSessionEvidence)
    assert refusal is None or isinstance(refusal, ImmutableTagRefusalEvidence)
    assert repository is None or isinstance(repository, EcrRepositoryEvidence)
    problems.extend(_joins(image, scan, session, refusal, repository, denials))
    return CommittedRunEvidence(
        image=image,
        scan=scan,
        session=session,
        refusal=refusal,
        repository=repository,
        denials=tuple(denials),
        problems=tuple(problems),
    )
