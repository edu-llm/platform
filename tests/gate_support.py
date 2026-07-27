"""Helpers shared by the acceptance-gate test modules.

Not collected by pytest: the filename deliberately does not start with ``test_``.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

from edullm_platform.criteria import CriterionSpec, CriterionStatus
from edullm_platform.evidence import EVIDENCE_STALE_CODE, GitHubPlanEvidence, ServiceQuotasEvidence
from edullm_platform.phase0_gate import Phase0Inputs, load_phase0_inputs

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_FILENAMES = ("github-plan.sanitized.json", "service-quotas.sanitized.json")


def _imported(module_path: Path) -> ModuleType:
    """The already-imported test module at ``module_path``, importing it if it is not."""
    for module in list(sys.modules.values()):
        filename = getattr(module, "__file__", None)
        if filename is not None and Path(filename) == module_path:
            return module
    spec = importlib.util.spec_from_file_location(module_path.stem, module_path)
    if spec is None or spec.loader is None:  # pragma: no cover - unreachable for a .py file
        raise ImportError(f"cannot import {module_path}")
    module = importlib.util.module_from_spec(spec)
    # Registered before execution because a module that is absent from sys.modules while
    # its own body runs breaks dataclass field resolution.
    sys.modules[module_path.stem] = module
    spec.loader.exec_module(module)
    return module


def fixtures_a_module_rests_on(module_path: Path) -> frozenset[Path]:
    """Every path under ``fixtures/`` the module at ``module_path`` holds, repo-relative.

    Found by importing the module and reading its ``Path`` values, so it follows a capture
    directory that is imported from ``edullm_platform`` exactly as it follows one spelled
    out in the test module. Nothing here reads a name, of a module or of a capture, which
    is what makes it survive a rename of either.
    """
    found: set[Path] = set()
    for value in vars(_imported(module_path)).values():
        if not isinstance(value, Path):
            continue
        candidate = value if value.is_absolute() else PROJECT_ROOT / value
        try:
            relative = candidate.resolve().relative_to(PROJECT_ROOT)
        except (OSError, ValueError):
            continue
        if relative.parts[:1] == ("fixtures",):
            found.add(relative)
    return frozenset(found)


def modules_cited_by(
    specs: Sequence[CriterionSpec],
    status: CriterionStatus,
) -> dict[str, tuple[Path, ...]]:
    """``{criterion number: the test module files its citations name}``, for one status."""
    cited: dict[str, tuple[Path, ...]] = {}
    for spec in specs:
        if spec.status is not status:
            continue
        modules = sorted({node_id.split("::", 1)[0] for node_id in spec.cited_node_ids})
        cited[spec.number] = tuple(PROJECT_ROOT / module for module in modules)
    return cited


def fixtures_backing(
    specs: Sequence[CriterionSpec],
    status: CriterionStatus,
) -> dict[str, frozenset[Path]]:
    """``{criterion number: the fixtures its cited tests rest on}``, for one status."""
    return {
        number: frozenset().union(
            *(fixtures_a_module_rests_on(module) for module in modules)
        )
        if modules
        else frozenset()
        for number, modules in modules_cited_by(specs, status).items()
    }


def evidence_not_in_the_tree(
    specs: Sequence[CriterionSpec],
    status: CriterionStatus = CriterionStatus.COVERED,
) -> tuple[str, ...]:
    """Everything the criteria of one status rest on that this checkout does not hold.

    A citation is worth what the tree behind it is worth. This resolves each citation to
    the module file it names, and each module to the fixtures it reads, and reports the
    ones that are not there -- so a criterion resting on a capture that never left the
    laptop it was taken on reads as what it is rather than as proof.
    """
    absent = [
        f"criterion {number} cites {module.relative_to(PROJECT_ROOT)}, "
        "which this checkout does not hold"
        for number, modules in modules_cited_by(specs, status).items()
        for module in modules
        if not module.is_file()
    ]
    if absent:
        # The fixture half imports these modules, which a missing file makes impossible.
        return tuple(sorted(absent))
    return tuple(
        sorted(
            f"criterion {number} rests on {fixture}, which is not committed"
            for number, fixtures in fixtures_backing(specs, status).items()
            for fixture in fixtures
            if not (PROJECT_ROOT / fixture).exists()
        )
    )


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


def run_gate(repo_root: Path, tool: str, **environment: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(repo_root / "src"), str(repo_root)])
    env.update(environment)
    return subprocess.run(
        [sys.executable, str(repo_root / "tools" / tool)],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def run_validate_phase0(repo_root: Path, **environment: str) -> subprocess.CompletedProcess[str]:
    return run_gate(repo_root, "validate_phase0.py", **environment)


def run_validate_phase1(repo_root: Path, **environment: str) -> subprocess.CompletedProcess[str]:
    return run_gate(repo_root, "validate_phase1.py", **environment)


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
