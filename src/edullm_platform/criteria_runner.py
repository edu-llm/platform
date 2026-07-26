"""Run the pytest node ids a phase's criterion cites, without recursing.

An acceptance gate runs pytest, and pytest runs tests of the acceptance gate. Four
things keep that from turning into an unbounded recursion or a hang:

1. Every pytest subprocess this module starts carries ``EDULLM_GATE_NESTED=1``.
   :func:`refuse_nested_execution` raises before any subprocess is started when that
   variable is already set, so a gate invoked from inside a gate's own test run stops
   instead of spawning another level. Depth is bounded at one, and the variable names no
   phase: a Phase 1 gate started from inside a Phase 0 criteria run is the same recursion
   and stops for the same reason.
2. A criterion may never cite a test from a module that invokes the gate or the proof
   generator. ``edullm_platform.criteria.REENTRANT_TEST_MODULES`` lists them and
   ``CriterionSpec`` rejects such a citation when the spec is constructed, so the guard
   above is never the only thing standing between the gate and itself.
3. Only explicit ``path::name`` node ids are ever passed to pytest. A bare directory,
   an empty selection, or anything else is refused, so "the gate runs pytest" cannot
   silently become "the gate runs the whole suite".
4. Every subprocess has a wall-clock timeout. A timeout is reported as an execution
   failure, which fails the criteria closed rather than hanging the gate.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from xml.etree import ElementTree

__all__ = [
    "COLLECT_TIMEOUT_SECONDS",
    "EXECUTION_TIMEOUT_SECONDS",
    "NESTED_GATE_ENV",
    "NestedExecutionError",
    "SelectionOutcome",
    "collect_node_ids",
    "refuse_nested_execution",
    "run_node_ids",
    "subprocess_environment",
]

NESTED_GATE_ENV: Final = "EDULLM_GATE_NESTED"
COLLECT_TIMEOUT_SECONDS: Final = 300.0
EXECUTION_TIMEOUT_SECONDS: Final = 1800.0

NESTED_EXECUTION_MESSAGE: Final = (
    "refusing to execute phase criteria from inside a phase criteria run. "
    f"{NESTED_GATE_ENV} is set, which means this process is already a child of a gate. "
    "Running a gate here would recurse."
)


class NestedExecutionError(RuntimeError):
    """The gate was asked to run pytest from inside its own pytest run."""


@dataclass(frozen=True)
class SelectionOutcome:
    """What happened to a set of requested node ids."""

    requested: frozenset[str]
    collected: frozenset[str]
    passed: frozenset[str]
    exit_code: int
    execution_error: str | None = None

    @property
    def missing(self) -> frozenset[str]:
        return self.requested - self.collected

    @property
    def failed(self) -> frozenset[str]:
        """Requested, collectable, and not observed passing.

        A test that errored, failed, was skipped, or was never reported at all lands
        here. The gate treats every one of those as a criterion it cannot rely on.
        """
        return (self.requested & self.collected) - self.passed


def subprocess_environment(base: Mapping[str, str] | None = None) -> dict[str, str]:
    environment = dict(os.environ if base is None else base)
    environment[NESTED_GATE_ENV] = "1"
    return environment


def refuse_nested_execution() -> None:
    if os.environ.get(NESTED_GATE_ENV):
        raise NestedExecutionError(NESTED_EXECUTION_MESSAGE)


def _run_pytest(
    repo_root: Path,
    arguments: Sequence[str],
    timeout: float,
) -> subprocess.CompletedProcess[str] | None:
    """Run pytest in a child process. ``None`` means the child hit the timeout."""
    try:
        return subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *arguments],
            cwd=repo_root,
            env=subprocess_environment(),
            capture_output=True,
            text=True,
            check=False,
            shell=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None


def collect_node_ids(
    repo_root: Path,
    *,
    timeout: float = COLLECT_TIMEOUT_SECONDS,
) -> frozenset[str]:
    """Every node id pytest can collect in ``repo_root``.

    A repository whose suite cannot be collected returns the empty set rather than
    raising. The caller reads that as "none of the cited tests exist", which is the
    honest reading and fails the criteria closed.
    """
    refuse_nested_execution()
    completed = _run_pytest(repo_root, ["--collect-only", "-q", "--no-header"], timeout)
    if completed is None or completed.returncode != 0:
        return frozenset()
    return frozenset(line.strip() for line in completed.stdout.splitlines() if "::" in line)


def _reconstruct_node_id(case: ElementTree.Element) -> str:
    module = case.get("classname", "").replace(".", "/")
    return f"{module}.py::{case.get('name', '')}"


def _passing_node_ids(report: Path) -> frozenset[str]:
    root = ElementTree.parse(report).getroot()
    return frozenset(
        _reconstruct_node_id(case)
        for case in root.iter("testcase")
        if case.find("failure") is None
        and case.find("error") is None
        and case.find("skipped") is None
    )


def run_node_ids(
    repo_root: Path,
    node_ids: Sequence[str],
    *,
    timeout: float = EXECUTION_TIMEOUT_SECONDS,
    collect_timeout: float = COLLECT_TIMEOUT_SECONDS,
) -> SelectionOutcome:
    """Execute the given node ids and report which of them passed.

    Node ids that pytest cannot collect are reported as missing and are never handed to
    pytest, because a single unknown node id makes pytest refuse the whole selection.
    """
    refuse_nested_execution()
    requested = frozenset(node_ids)
    malformed = sorted(node_id for node_id in requested if "::" not in node_id)
    if malformed:
        raise ValueError(
            "the criteria runner only ever selects explicit pytest node ids; refusing "
            f"{malformed!r}"
        )
    collected = collect_node_ids(repo_root, timeout=collect_timeout)
    selectable = sorted(requested & collected)
    if not selectable:
        return SelectionOutcome(
            requested=requested,
            collected=collected,
            passed=frozenset(),
            exit_code=0,
        )
    with tempfile.TemporaryDirectory() as workspace:
        report = Path(workspace) / "criteria.xml"
        completed = _run_pytest(
            repo_root,
            ["-q", "--no-header", "--tb=no", f"--junitxml={report}", *selectable],
            timeout,
        )
        if completed is None:
            return SelectionOutcome(
                requested=requested,
                collected=collected,
                passed=frozenset(),
                exit_code=-1,
                execution_error=(
                    f"pytest did not finish within {timeout:.0f}s while executing "
                    f"{len(selectable)} cited node ids"
                ),
            )
        if not report.exists():
            detail = completed.stderr.strip() or completed.stdout.strip()
            return SelectionOutcome(
                requested=requested,
                collected=collected,
                passed=frozenset(),
                exit_code=completed.returncode,
                execution_error=f"pytest did not report on the cited node ids: {detail}",
            )
        return SelectionOutcome(
            requested=requested,
            collected=collected,
            passed=_passing_node_ids(report),
            exit_code=completed.returncode,
        )
