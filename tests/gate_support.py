"""Helpers shared by the acceptance-gate test modules.

Not collected by pytest: the filename deliberately does not start with ``test_``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from edullm_platform.evidence import EVIDENCE_STALE_CODE, GitHubPlanEvidence, ServiceQuotasEvidence
from edullm_platform.phase0_gate import Phase0Inputs, load_phase0_inputs

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_FILENAMES = ("github-plan.sanitized.json", "service-quotas.sanitized.json")


def recent_observed_at() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def refresh_evidence_files(repo_root: Path, *, observed_at: str | None = None) -> None:
    timestamp = observed_at or recent_observed_at()
    for filename in EVIDENCE_FILENAMES:
        evidence_path = repo_root / "fixtures" / "evidence" / filename
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
        payload["observed_at"] = timestamp
        evidence_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def synthetic_account_id_alias() -> str:
    digits = bytes.fromhex("393233383437313632303934").decode()
    return f"acct-{digits}-prod"


def loaded_inputs() -> Phase0Inputs:
    inputs = load_phase0_inputs(PROJECT_ROOT)
    if (
        inputs.github_plan_load_error != EVIDENCE_STALE_CODE
        and inputs.aws_capacity_load_error != EVIDENCE_STALE_CODE
    ):
        return inputs
    fresh_at = recent_observed_at()
    github_plan = inputs.github_plan
    github_plan_load_error = inputs.github_plan_load_error
    if github_plan_load_error == EVIDENCE_STALE_CODE:
        payload = json.loads(
            (
                PROJECT_ROOT / "fixtures" / "evidence" / "github-plan.sanitized.json"
            ).read_text(encoding="utf-8")
        )
        payload["observed_at"] = fresh_at
        github_plan = GitHubPlanEvidence.model_validate(payload)
        github_plan_load_error = None
    aws_capacity = inputs.aws_capacity
    aws_capacity_load_error = inputs.aws_capacity_load_error
    if aws_capacity_load_error == EVIDENCE_STALE_CODE:
        payload = json.loads(
            (
                PROJECT_ROOT / "fixtures" / "evidence" / "service-quotas.sanitized.json"
            ).read_text(encoding="utf-8")
        )
        payload["observed_at"] = fresh_at
        aws_capacity = ServiceQuotasEvidence.model_validate(payload)
        aws_capacity_load_error = None
    return replace(
        inputs,
        github_plan=github_plan,
        github_plan_load_error=github_plan_load_error,
        aws_capacity=aws_capacity,
        aws_capacity_load_error=aws_capacity_load_error,
    )


def run_validate_phase0(repo_root: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(repo_root / "src"), str(repo_root)])
    return subprocess.run(
        [sys.executable, str(repo_root / "tools" / "validate_phase0.py")],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def copy_gate_repo(destination: Path) -> Path:
    """A checkout with configuration, fixtures, library, and tools but no test suite.

    The absent ``tests/`` directory is deliberate: it is what makes these copies cheap,
    and it is also the state the gate has to fail closed on, because a tree with no
    tests cannot prove a single criterion.
    """
    repo_root = destination / "repo"
    for relative in (
        "config",
        "fixtures",
        "src",
        "tools",
        "pyproject.toml",
    ):
        source = PROJECT_ROOT / relative
        target = repo_root / relative
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    refresh_evidence_files(repo_root)
    return repo_root
