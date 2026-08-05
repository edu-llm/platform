"""Helpers the operational inventory tests share.

Not collected by pytest: the filename deliberately does not start with ``test_``.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from edullm_platform.evidence import EVIDENCE_STALE_CODE, GitHubPlanEvidence, ServiceQuotasEvidence
from edullm_platform.operational_inventory import InventoryInputs, load_inventory_inputs

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


def loaded_inputs() -> InventoryInputs:
    inputs = load_inventory_inputs(PROJECT_ROOT)
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


def copy_inventory_repo(destination: Path) -> Path:
    """A checkout with configuration, fixtures, library, and tools but no test suite.

    The absent ``tests/`` directory is deliberate and is what makes these copies cheap.
    Nothing the checks read lives under it.
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
