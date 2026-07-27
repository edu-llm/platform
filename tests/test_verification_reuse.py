"""One tree is verified once per process, and the shortcut cannot skip a verification.

Every proof generator verifies the tree it is describing by running the whole suite in a
child pytest, and one session runs several generators against one unchanged tree. The
second and third of those runs measure exactly what the first one did.

The saving is only acceptable if a bundle still reports a full suite that genuinely ran.
Two properties are what make that true, and both are pinned here. The memory is
process-local and never written to disk, so a pass recorded before a change can never be
found again after it. And it is keyed on the resolved repository root together with the
ignore list, so a run against a different tree — including the temporary ones these tests
build — always misses and measures for itself.

Nothing here starts a real pytest child. The child is replaced by a recorder, so what
these tests observe is how many times the generator machinery *would* have spawned one.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from edullm_platform import proof_bundle
from edullm_platform.proof_bundle import run_full_suite

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NESTED_ENV = "EDULLM_TEST_NESTED"

#: What a process that has just started knows about any tree: nothing.
PROBE = (
    "from edullm_platform import proof_bundle\n"
    "print(proof_bundle.full_suite_child_runs(), len(proof_bundle._FULL_SUITE_CACHE))\n"
)

REPORT = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<testsuites><testsuite name="pytest" errors="0" failures="{failures}" skipped="0" '
    'tests="7"></testsuite></testsuites>\n'
)


@dataclass
class SpawnRecorder:
    """Stands in for the pytest child and records which tree it was asked to measure."""

    failures: int = 0
    roots: list[Path] = field(default_factory=list)

    def __call__(
        self,
        repo_root: Path,
        arguments: Sequence[str],
        *,
        nested_env: str,
    ) -> subprocess.CompletedProcess[str]:
        self.roots.append(repo_root)
        report = next(
            argument.removeprefix("--junitxml=")
            for argument in arguments
            if argument.startswith("--junitxml=")
        )
        Path(report).write_text(REPORT.format(failures=self.failures), encoding="utf-8")
        return subprocess.CompletedProcess(
            args=["pytest"],
            returncode=1 if self.failures else 0,
            stdout="",
            stderr="",
        )


@pytest.fixture
def spawns(monkeypatch: pytest.MonkeyPatch) -> SpawnRecorder:
    """An empty memory, a reset counter, and a recorder in place of the pytest child.

    Both module-level values are replaced rather than mutated, so this fixture cannot
    leave a fabricated outcome behind for a generator running later in the same session
    to mistake for a verification, and its fake spawns stay invisible to the session
    budget in ``tests/test_suite_budget.py``.
    """
    monkeypatch.setattr(proof_bundle, "_FULL_SUITE_CACHE", {})
    monkeypatch.setattr(proof_bundle, "_full_suite_child_runs", 0)
    recorder = SpawnRecorder()
    monkeypatch.setattr(proof_bundle, "run_pytest", recorder)
    return recorder


def test_a_second_run_against_the_same_tree_starts_no_child(
    spawns: SpawnRecorder,
    tmp_path: Path,
) -> None:
    first = run_full_suite(tmp_path, nested_env=NESTED_ENV)
    second = run_full_suite(tmp_path, nested_env=NESTED_ENV)

    assert spawns.roots == [tmp_path]
    assert proof_bundle.full_suite_child_runs() == 1
    assert second == first
    assert first.tests == 7
    assert first.green


def test_a_run_against_a_different_tree_starts_its_own_child(
    spawns: SpawnRecorder,
    tmp_path: Path,
) -> None:
    one = tmp_path / "one"
    another = tmp_path / "another"
    one.mkdir()
    another.mkdir()

    run_full_suite(one, nested_env=NESTED_ENV)
    run_full_suite(another, nested_env=NESTED_ENV)

    assert spawns.roots == [one, another]
    assert proof_bundle.full_suite_child_runs() == 2


def test_two_spellings_of_one_tree_are_the_same_tree(
    spawns: SpawnRecorder,
    tmp_path: Path,
) -> None:
    # The key is the resolved root, so a generator reaching the repository by a different
    # route than the last one is not a reason to measure the same tree again.
    (tmp_path / "inside").mkdir()

    run_full_suite(tmp_path, nested_env=NESTED_ENV)
    run_full_suite(tmp_path / "inside" / "..", nested_env=NESTED_ENV)

    assert proof_bundle.full_suite_child_runs() == 1


def test_a_different_ignore_list_is_a_different_question(
    spawns: SpawnRecorder,
    tmp_path: Path,
) -> None:
    # Excluding a different set of modules is a different suite, and answering it from
    # memory would report a count for tests that were never run under that exclusion.
    run_full_suite(tmp_path, nested_env=NESTED_ENV)
    run_full_suite(tmp_path, nested_env=NESTED_ENV, ignore=("tests/test_manifest.py",))

    assert proof_bundle.full_suite_child_runs() == 2


def test_a_failing_suite_is_remembered_as_the_failure_it_was(
    spawns: SpawnRecorder,
    tmp_path: Path,
) -> None:
    # The shortcut must not be able to turn red into green on the second reading.
    spawns.failures = 1

    outcome = run_full_suite(tmp_path, nested_env=NESTED_ENV)
    again = run_full_suite(tmp_path, nested_env=NESTED_ENV)

    assert not outcome.green
    assert again == outcome
    assert proof_bundle.full_suite_child_runs() == 1


def test_a_new_process_has_verified_nothing() -> None:
    """The memory dies with the process, so it can never validate a tree it never saw.

    This is the property that makes the shortcut safe to keep. A verification remembered
    on disk would be found again after the tree it described had changed, and the bundle
    would then report a pass for a suite that never ran against what it describes.
    """
    completed = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
        shell=False,
    )

    assert completed.stdout.strip() == "0 0"
