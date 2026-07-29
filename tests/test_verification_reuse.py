"""One tree is verified once per process, and no shortcut can skip a verification.

Every proof generator verifies the tree it is describing by running the whole suite in a
child pytest, and one session runs several generators against one unchanged tree. The
second and third of those runs measure exactly what the first one did.

The saving is only acceptable if a bundle still reports a full suite that genuinely ran.
Two properties are what make that true, and both are pinned here. The memory is
process-local and never written to disk, so a pass recorded before a change can never be
found again after it. And it is keyed on the resolved repository root together with the
ignore list, so a run against a different tree — including the temporary ones these tests
build — always misses and measures for itself.

The suite reproduces a recorded suite result only when asked, and nightly rather than on
every pull request. That saving is bounded to the *tests*: a generator writing a bundle
always measures, because nothing on its command line can ask it not to. That is asserted
here too, beside the memory, because the two are the only ways a written bundle could come
to carry a count nobody took.

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
from edullm_platform.proof_bundle import (
    GENERATOR_NESTED_ENV_VARS,
    collect_node_ids,
    pytest_environment,
    run_full_suite,
)
from edullm_platform.proof_generator import parse_generator_args
from tools import (
    build_phase0_proof,
    build_phase1_proof,
    build_phase2_proof,
    build_phase3_proof,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NESTED_ENV = "EDULLM_TEST_NESTED"
COLLECTED = "tests/test_mini.py::test_mini_passes\ntests/test_mini.py::test_mini_also_passes\n"

#: What a process that has just started knows about any tree: nothing.
PROBE = (
    "from edullm_platform import proof_bundle\n"
    "print(\n"
    "    proof_bundle.full_suite_child_runs(),\n"
    "    proof_bundle.collection_child_runs(),\n"
    "    len(proof_bundle._FULL_SUITE_CACHE),\n"
    "    len(proof_bundle._COLLECTION_CACHE),\n"
    ")\n"
)

REPORT = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<testsuites><testsuite name="pytest" errors="0" failures="{failures}" skipped="0" '
    'tests="7"></testsuite></testsuites>\n'
)


@dataclass
class SpawnRecorder:
    """Stands in for the pytest child and records which tree it was asked about."""

    failures: int = 0
    roots: list[Path] = field(default_factory=list)
    collections: list[Path] = field(default_factory=list)

    def __call__(
        self,
        repo_root: Path,
        arguments: Sequence[str],
        *,
        nested_env: str,
    ) -> subprocess.CompletedProcess[str]:
        if "--collect-only" in arguments:
            self.collections.append(repo_root)
            return subprocess.CompletedProcess(
                args=["pytest"], returncode=0, stdout=COLLECTED, stderr=""
            )
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
    monkeypatch.setattr(proof_bundle, "_COLLECTION_CACHE", {})
    monkeypatch.setattr(proof_bundle, "_full_suite_child_runs", 0)
    monkeypatch.setattr(proof_bundle, "_collection_child_runs", 0)
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


# --------------------------------------------------------------------------------------
# One nested environment, so the two children are the same child
# --------------------------------------------------------------------------------------


def test_every_nested_run_carries_every_generators_guard() -> None:
    # This is what makes sharing a measurement trivially correct rather than an argument
    # about which variable the child happened to be missing.
    environment = pytest_environment(build_phase0_proof.NESTED_RUN_ENV)

    assert GENERATOR_NESTED_ENV_VARS
    assert [environment[variable] for variable in GENERATOR_NESTED_ENV_VARS] == ["1"] * len(
        GENERATOR_NESTED_ENV_VARS
    )


def test_every_generator_asks_for_the_same_environment() -> None:
    asked = [
        pytest_environment(generator.NESTED_RUN_ENV)
        for generator in (
            build_phase0_proof,
            build_phase1_proof,
            build_phase2_proof,
            build_phase3_proof,
        )
    ]

    assert len(asked) == len(GENERATOR_NESTED_ENV_VARS)
    assert all(environment == asked[0] for environment in asked)


def test_every_generators_guard_is_one_of_the_variables_that_gets_set() -> None:
    # A generator whose guard is left out of the shared list would be the one difference
    # between two children that are otherwise identical, and it would not refuse inside
    # another generator's verification run.
    guards = {
        build_phase0_proof.NESTED_RUN_ENV,
        build_phase1_proof.NESTED_RUN_ENV,
        build_phase2_proof.NESTED_RUN_ENV,
        build_phase3_proof.NESTED_RUN_ENV,
    }

    assert guards == set(GENERATOR_NESTED_ENV_VARS)


def test_no_generator_cli_can_be_asked_to_skip_reproduction() -> None:
    """A bundle a generator writes always carries counts that generator measured.

    The tests reproduce only when asked; the generators never get the choice, and the way
    that is kept true is that there is no option to. A ``--no-verify`` or a
    ``--reuse-verification`` appearing here would make the committed bundles stop being
    evidence that anybody ran anything, and this is the only place that would notice.
    """
    accepted = {"output_dir", "generated_at", "regenerate_goldens"}

    shared = parse_generator_args([], description="phases 1 to 3")
    phase0 = build_phase0_proof.parse_args([])

    assert set(vars(shared)) == accepted
    assert set(vars(phase0)) == accepted


def test_every_generator_module_is_listed_as_one() -> None:
    # The two registries have to name the same set of generators. A module in one and not
    # the other is a generator whose tests run inside another generator's verification --
    # bounded, because each build excludes its own tests, and quadratic in wall clock.
    generators = {
        build_phase0_proof.GENERATOR_TEST_PATH,
        build_phase1_proof.GENERATOR_TEST_PATH,
        build_phase2_proof.GENERATOR_TEST_PATH,
        build_phase3_proof.GENERATOR_TEST_PATH,
    }

    assert generators == set(proof_bundle.GENERATOR_TEST_PATHS)


# --------------------------------------------------------------------------------------
# Collection, on the same terms
# --------------------------------------------------------------------------------------


def test_a_second_collection_of_the_same_tree_starts_no_child(
    spawns: SpawnRecorder,
    tmp_path: Path,
) -> None:
    first = collect_node_ids(tmp_path, nested_env=NESTED_ENV)
    second = collect_node_ids(tmp_path, nested_env=NESTED_ENV)

    assert spawns.collections == [tmp_path]
    assert second == first
    assert first == tuple(COLLECTED.split())


def test_collecting_a_different_tree_starts_its_own_child(
    spawns: SpawnRecorder,
    tmp_path: Path,
) -> None:
    one = tmp_path / "one"
    another = tmp_path / "another"
    one.mkdir()
    another.mkdir()

    collect_node_ids(one, nested_env=NESTED_ENV)
    collect_node_ids(another, nested_env=NESTED_ENV)

    assert spawns.collections == [one, another]


@pytest.mark.slow
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

    assert completed.stdout.strip() == "0 0 0 0"
