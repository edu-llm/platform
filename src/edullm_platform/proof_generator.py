"""What every proof generator does the same way, extracted once and measured first.

**The measurement came before the extraction, and it changed what got extracted.** The
master plan argued the four generators are 6,462 lines of duplication and that generalising
them saves "on the order of ten thousand lines" across Phases 4 to 9. Compared function by
function, that is not what they are.

Of the 6,964 lines in the four generators, only 104 are byte-identical across files. Every
other same-named function has a different body, and the differences are mostly *prose*:
``render_index`` runs 133 to 176 lines and is 60% common; ``known_limitations`` runs 88 to
112 and is 20% common; ``input_digest_table`` is 11% common. Summed over every same-named
function, the textually shared fraction of the whole is **23%** -- about 1,580 lines, not
6,462.

So this module is the part that is genuinely one thing, and it stops there. The rule applied
is the one the Phase 4 plan wrote for itself: *if the generalised generator needs a fifth
per-phase hook beyond criteria module, artifact list, capture directory and golden set, the
abstraction is wrong -- ship three shared helpers instead of one framework and stop.* What
follows is those helpers.

**What is here.** The CLI shell, the nested-verification run, the per-module coverage
scoping, the unit-test report, the golden pair's write-and-drift-check, and the two verdict
sentences. All of it is identical across phases 1 to 3 except for a phase number and, in
the report, two sentences and an optional caveat -- so those are parameters and nothing
else is.

**What is deliberately not here.** ``render_index``, ``known_limitations``,
``render_matrix``, ``render_schema_report``, ``compute_goldens`` and every phase-specific
renderer. They share a name and a job and not their content, and a shared version would be
a template with more parameters than body -- which reads worse than four honest functions
and makes the next phase's prose an argument to a call rather than a paragraph somebody
wrote.

**Phase 0 uses only the CLI half.** It scopes its verification by *fixture* rather than by
test module, which is a real difference rather than an accident: Phase 0 is contracts, so
what its bundle is about is which fixture each test ran against.

**The acceptance test for anything in here is byte-identical bundles.** The committed
goldens mean a correct extraction produces the same bytes for all four phases, and
``proof_bundle.py`` refuses a drifted golden. That is a stronger oracle than a passing
suite, and it is why this could be done at all.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from edullm_platform.criteria import (
    REENTRANT_TEST_MODULES,
    CriteriaDefinitionError,
    CriterionSpec,
)
from edullm_platform.proof_bundle import (
    GENERATOR_TEST_PATHS,
    MissingTestNodeError,
    ProofBundleError,
    SuiteOutcome,
    bullets,
    collect_node_ids,
    command_block,
    run_full_suite,
    run_test_selection,
    table,
)

__all__ = [
    "BUNDLE_SCHEMA_VERSION",
    "GOLDENS_FILENAME",
    "GOLDENS_REPORT_FILENAME",
    "Coherence",
    "ModuleCoverage",
    "Verification",
    "bundle_directory",
    "establish_coherence",
    "gate_verdict",
    "goldens_path",
    "module_scoped_node_ids",
    "parse_generator_args",
    "phase_test_modules",
    "render_unit_test_report",
    "run_generator_cli",
    "standing",
    "verify_repository",
]

BUNDLE_SCHEMA_VERSION: Final = 1
GOLDENS_FILENAME: Final = "serialization-goldens.json"
GOLDENS_REPORT_FILENAME: Final = "serialization-goldens.md"


@dataclass(frozen=True)
class ModuleCoverage:
    module: str
    node_ids: tuple[str, ...]


@dataclass(frozen=True)
class Coherence:
    """What the tree says about a phase's citations, established without running a test.

    Everything here comes from one ``--collect-only`` child, which costs about a second
    against a tree that takes minutes to execute. That is the whole reason the split
    exists: the questions this answers -- do the cited node ids resolve, which tests does
    the phase own, what would the targeted run select -- are the questions a change is
    most likely to get wrong, and none of them needs a test to be executed to answer.

    A citation that no longer resolves is refused here rather than reported, because a
    matrix claiming coverage it cannot run is wrong before anybody runs anything.
    """

    collected_node_ids: tuple[str, ...]
    selected_node_ids: tuple[str, ...]
    module_coverage: tuple[ModuleCoverage, ...]


@dataclass(frozen=True)
class Verification:
    """A coherent phase whose recorded suite result was also reproduced by running it.

    The coherence fields are carried flat rather than nested so that every renderer keeps
    reading them the way it always has. What this adds over :class:`Coherence` is the two
    outcomes, and they answer different questions. ``selected`` is the targeted run over
    every cited node id plus every test the phase added, which is what the bundle's
    coverage claims rest on. ``full_suite`` is the whole tree, which is what stops a phase
    reporting itself green while breaking somebody else's tests.

    A bundle may only be rendered from one of these, never from a :class:`Coherence`. A
    document that printed a suite count nobody measured would be the exact failure the
    rest of this package is built to prevent.
    """

    collected_node_ids: tuple[str, ...]
    selected_node_ids: tuple[str, ...]
    failed_node_ids: tuple[str, ...]
    selected: SuiteOutcome
    full_suite: SuiteOutcome
    module_coverage: tuple[ModuleCoverage, ...]


def bundle_directory(repo_root: Path, phase: str) -> Path:
    return repo_root / "proof" / phase


def goldens_path(output_dir: Path) -> Path:
    return output_dir / GOLDENS_FILENAME


def phase_test_modules(collected: Sequence[str], prefixes: tuple[str, ...]) -> tuple[str, ...]:
    """Which test modules this phase added, minus the ones that would recurse.

    ``REENTRANT_TEST_MODULES`` is subtracted here rather than filtered later because a
    generator that selected a test invoking a generator would run itself, and the failure
    is a hang rather than an error.
    """
    modules = {
        node_id.split("::", 1)[0] for node_id in collected if node_id.startswith(prefixes)
    }
    return tuple(sorted(modules - set(REENTRANT_TEST_MODULES)))


def module_scoped_node_ids(
    collected: Sequence[str], prefixes: tuple[str, ...]
) -> tuple[ModuleCoverage, ...]:
    return tuple(
        ModuleCoverage(
            module=module,
            node_ids=tuple(
                sorted(node_id for node_id in collected if node_id.startswith(f"{module}::"))
            ),
        )
        for module in phase_test_modules(collected, prefixes)
    )


def establish_coherence(
    repo_root: Path,
    *,
    criteria: Sequence[CriterionSpec],
    nested_env: str,
    test_prefixes: tuple[str, ...],
) -> Coherence:
    """Collect, check the citations resolve, and work out what a run would select.

    Both refusals live here rather than after the run, and refuse rather than report. A
    matrix citing a node id pytest does not collect is claiming coverage it cannot run,
    and the only useful moment to say so is before the bundle exists to be believed. A
    selection that would re-enter the generator has to be stopped before it is handed to
    pytest, because that failure presents as a machine that has stopped responding rather
    than as an error.
    """
    collected = collect_node_ids(repo_root, nested_env=nested_env)
    cited = {node_id for check in criteria for node_id in check.cited_node_ids}
    missing = sorted(cited - set(collected))
    if missing:
        raise MissingTestNodeError(
            "the negative-case matrix cites test node ids that pytest does not collect; "
            "a matrix may not claim coverage it cannot run:\n  " + "\n  ".join(missing)
        )
    coverage = module_scoped_node_ids(collected, test_prefixes)
    selected = tuple(sorted(cited | {node_id for entry in coverage for node_id in entry.node_ids}))
    reentrant = sorted(
        node_id for node_id in selected if node_id.split("::", 1)[0] in REENTRANT_TEST_MODULES
    )
    if reentrant:
        raise ProofBundleError(
            "the proof generator must not select a test that invokes the generator or the "
            "acceptance gate, which would recurse:\n  " + "\n  ".join(reentrant)
        )
    return Coherence(
        collected_node_ids=collected,
        selected_node_ids=selected,
        module_coverage=coverage,
    )


def verify_repository(
    repo_root: Path,
    *,
    criteria: Sequence[CriterionSpec],
    nested_env: str,
    test_prefixes: tuple[str, ...],
) -> Verification:
    """Establish coherence, then reproduce: run the selection and the whole suite.

    This is the expensive half and the only half that executes anything. Everything a
    caller can learn without running a test has already been learned, and refused on,
    by :func:`establish_coherence`.
    """
    found = establish_coherence(
        repo_root, criteria=criteria, nested_env=nested_env, test_prefixes=test_prefixes
    )
    outcome, failed = run_test_selection(
        repo_root, found.selected_node_ids, nested_env=nested_env
    )
    return Verification(
        collected_node_ids=found.collected_node_ids,
        selected_node_ids=found.selected_node_ids,
        failed_node_ids=failed,
        selected=outcome,
        full_suite=run_full_suite(repo_root, nested_env=nested_env),
        module_coverage=found.module_coverage,
    )


def render_unit_test_report(
    verification: Verification,
    *,
    phase_number: int,
    verification_commands: Sequence[str],
    caveat: str | None = None,
) -> str:
    """The counts, and what they do and do not establish.

    ``caveat`` is a parameter and not a constant because what it says is a fact about a
    particular phase's history. Phase 2's names the workflow Phase 1 shipped that could not
    complete a run; Phase 3's adds the state machine Phase 2 shipped that could not complete
    an execution. Folding those into one sentence would make the warning generic, and a
    generic warning about green suites is one nobody reads twice.
    """
    full = verification.full_suite
    selected = verification.selected
    rows = [
        [entry.module, str(len(entry.node_ids)), "pass" if selected.green else "see below"]
        for entry in verification.module_coverage
    ]
    sections = [
        f"# Phase {phase_number} unit-test report",
        "",
        (
            "Summarised counts only. Raw pytest output is not copied here; the commands below "
            "reproduce it in full."
        ),
        "",
        "## Commands a reviewer can re-run",
        "",
        command_block(verification_commands),
        "",
        "## Whole suite",
        "",
        table(
            ["measure", "count"],
            [
                ["collected by pytest", str(len(verification.collected_node_ids))],
                [f"executed (excluding {', '.join(GENERATOR_TEST_PATHS)})", str(full.tests)],
                ["passed", str(full.passed)],
                ["failed", str(full.failures)],
                ["errored", str(full.errors)],
                ["skipped", str(full.skipped)],
                ["pytest exit code", str(full.exit_code)],
            ],
        ),
        "",
        "## Targeted verification run",
        "",
        (
            "Every test node id cited by the negative-case matrix, plus every test in the "
            f"modules Phase {phase_number} added, executed as one selection."
        ),
        "",
        table(
            ["measure", "count"],
            [
                ["selected node ids", str(len(verification.selected_node_ids))],
                ["executed", str(selected.tests)],
                ["passed", str(selected.passed)],
                ["failed", str(selected.failures)],
                ["errored", str(selected.errors)],
                ["skipped", str(selected.skipped)],
                ["pytest exit code", str(selected.exit_code)],
            ],
        ),
        "",
        "## Per-module coverage",
        "",
        (
            f"The test modules Phase {phase_number} added, excluding the "
            f"{'two' if phase_number == 1 else 'ones'} that invoke a gate or this "
            "generator; those run in the reviewer's own `uv run pytest -q`."
        ),
        "",
        table(["module", "tests", "result"], rows),
    ]
    if caveat is not None:
        sections.extend(["", caveat])
    if verification.failed_node_ids:
        sections.extend(["", "## Failures", "", bullets(verification.failed_node_ids)])
    return "\n".join(sections) + "\n"


def standing(gap_numbers: Sequence[str]) -> str:
    """How the bundle opens, which cannot be a fixed sentence about being unfinished.

    The first version of this said "It is not done", which was true when it was written and
    would have gone on being printed after it stopped being true. A reviewer who trusts the
    bundle would have been told the opposite of what the table below it says.
    """
    if gap_numbers:
        return "It is not done, and the Result table below says by how much."
    return (
        "Every criterion is covered and the gate is green, which is the state in which a "
        "bundle is most worth reading carefully: the Known limitations below say what each "
        "criterion does not cover, and `open-decisions.md` says what this phase surfaced and "
        "did not settle."
    )


def gate_verdict(gap_numbers: Sequence[str], *, phase_number: int) -> str:
    """What the gate does against this tree, said in the bundle rather than left to be run.

    A gap is reported as the honest state of the phase rather than as a broken gate, because
    the alternative is a reader who sees exit 1 and goes looking for the defect in the gate.
    """
    tool = f"`tools/validate_phase{phase_number}.py`"
    if not gap_numbers:
        return f"{tool} exits 0 against this tree: every phase criterion is covered or explicitly deferred."
    if len(gap_numbers) == 1:
        subject = f"criterion {gap_numbers[0]} is a GAP"
    else:
        subject = f"criteria {', '.join(gap_numbers)} are GAPs"
    return (
        f"{tool} exits 1 against this tree. Phase {phase_number} is not accepted: "
        f"{subject}. That is the honest state of the phase, not a broken gate. Read the Gaps "
        "section of `negative-case-matrix.md` for what closes it."
    )


def parse_generator_args(argv: Sequence[str] | None, *, description: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--regenerate-goldens", action="store_true")
    return parser.parse_args(argv)


def run_generator_cli(
    argv: Sequence[str] | None,
    *,
    description: str,
    repo_root: Path,
    nested_env: str,
    default_output_dir: Callable[[Path], Path],
    build: Callable[..., Sequence[Path]],
) -> int:
    """The CLI every generator has, including the guard that stops it running inside itself.

    The nested-run guard is the one part that must not be forgotten by a new generator, and
    is the reason this is shared rather than copied: the generator runs the whole suite, the
    suite includes a test that runs the generator, and without the guard that is unbounded
    recursion presenting as a machine that has stopped responding.
    """
    if os.environ.get(nested_env):
        print(
            "refusing to build the proof bundle from inside its own verification run",
            file=sys.stderr,
        )
        return 2
    args = parse_generator_args(argv, description=description)
    output_dir = default_output_dir(repo_root) if args.output_dir is None else Path(args.output_dir)
    generated_at = (
        datetime.now(tz=UTC)
        if args.generated_at is None
        else datetime.fromisoformat(args.generated_at)
    )
    try:
        written = build(
            repo_root,
            output_dir,
            generated_at=generated_at,
            regenerate_goldens=args.regenerate_goldens,
        )
    except (ProofBundleError, CriteriaDefinitionError) as error:
        print(str(error), file=sys.stderr)
        return 1
    for path in written:
        print(path.relative_to(repo_root) if path.is_relative_to(repo_root) else path)
    return 0
