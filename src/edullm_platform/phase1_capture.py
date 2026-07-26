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

from edullm_platform.evidence import FRESHNESS_WINDOW, evidence_load_reason_code
from edullm_platform.phase1_evidence import DeployedRoleEvidence
from edullm_platform.role_drift import (
    COMMITTED_ROLE_TEMPLATES,
    RoleDriftReport,
    compare_role_to_template,
    load_template_roles,
)

__all__ = [
    "CAPTURE_PARTITION",
    "CAPTURE_REGION",
    "CAPTURE_SUFFIX",
    "RECAPTURE_GUIDANCE",
    "ROLE_CAPTURE_DIR",
    "CaptureVerdict",
    "CommittedRoleCapture",
    "captures_that_do_not_hold",
    "read_committed_role_captures",
]

#: Where a capture lives once somebody has read it and decided to commit it. Beside the
#: Phase 0 evidence rather than under ``proof/``: a bundle is generated and this is not.
ROLE_CAPTURE_DIR: Final = Path("fixtures") / "evidence" / "phase-1" / "roles"
CAPTURE_SUFFIX: Final = ".sanitized.json"

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
    """What a committed capture is worth right now.

    ``STALE`` and ``INVALID`` carry the reason codes ``evidence_load_reason_code``
    returns, so a Phase 1 capture failure reads the same as the Phase 0 inventory's.
    """

    #: Loaded, inside its window, and identical to the template that declares the role.
    OK = "ok"
    #: No record is committed for a role a template declares.
    ABSENT = "capture_absent"
    #: A record is committed and is older than the freshness window.
    STALE = "evidence_stale"
    #: A record is committed and is not a role capture the contract accepts.
    INVALID = "evidence_invalid"
    #: A record loaded and disagrees with the template that declares the role.
    DRIFTED = "role_drift"
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
    verdict: CaptureVerdict
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
            verdict=CaptureVerdict.INVALID,
            detail=f"{capture_path} is not readable JSON, so it records nothing.",
        )
    try:
        evidence = DeployedRoleEvidence.model_validate(payload)
    except ValidationError as error:
        # The filename is all there is to go on: a record that failed its contract may
        # not have a role name, and one that has one may have any string in the field.
        reason = evidence_load_reason_code(error)
        stale = reason == CaptureVerdict.STALE.value
        return CommittedRoleCapture(
            role_name=named,
            template_path=templates.get(named),
            capture_path=capture_path,
            verdict=CaptureVerdict.STALE if stale else CaptureVerdict.INVALID,
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
    return CommittedRoleCapture(
        role_name=evidence.role_name,
        template_path=relative_path,
        capture_path=capture_path,
        verdict=CaptureVerdict.OK if report.matches else CaptureVerdict.DRIFTED,
        detail=(
            f"The deployed {evidence.role_name} matches {relative_path} as observed on "
            f"{evidence.observed_at.date().isoformat()}."
            if report.matches
            else _drift_detail(report)
        ),
        evidence=evidence,
        report=report,
    )


def read_committed_role_captures(
    repo_root: Path,
    *,
    capture_dir: Path | None = None,
    partition: str = CAPTURE_PARTITION,
    region: str = CAPTURE_REGION,
) -> tuple[CommittedRoleCapture, ...]:
    """Every role a template declares, plus any captured role none of them does.

    Driven by the template list rather than by the directory, so a capture somebody
    deleted is reported as a role nobody has looked at instead of vanishing from the
    answer. A record naming a role no template declares is reported too, for the same
    reason in the other direction.

    A template this module cannot project raises rather than producing a verdict: that is
    a defect in the repository rather than a fact about the account, and reporting it as a
    capture that does not hold would point the reader at the wrong half.
    """
    templates = dict(COMMITTED_ROLE_TEMPLATES)
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
            verdict=CaptureVerdict.ABSENT,
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
