"""Build every phase's proof bundle in one process, so one suite run serves all five.

WHY THIS EXISTS, AND WHY IT IS NOT A NEW GENERATOR. Each of the five generators verifies
the tree it describes by running the whole suite in a child pytest, and
:func:`edullm_platform.proof_bundle.run_full_suite` already keeps that answer against the
tree it was measured on so the second generator in a session reuses the first one's run.
That memory is process-local, deliberately -- ``proof_bundle.py`` says why -- and five
command lines are five processes, so the cache had nothing to share and every generator
measured the same unchanged tree again. Measured: about 2m37s per full-suite child on an
idle machine, five times, for a regeneration that has to happen whenever a config change
moves a digest.

Giving them one process is the whole of the fix. Nothing about what a bundle contains
changes: this calls the same ``build_bundle`` each generator's own command calls, with the
same arguments, and the only thing the five now share is the one verification they were
each independently asking for. One collection child and one full-suite child serve all
five; the targeted selection run stays per-phase, because each phase selects a different
set of node ids and that is a different question.

**The five individual commands still work and are still the documented ones for a single
phase.** This is an addition, not a replacement. A phase whose bundle alone needs
regenerating should not pay for the other four.

WHAT IS DELIBERATELY NOT HERE. No option that skips, reuses or shortens a verification.
``tests/test_verification_reuse.py`` holds that to every generator CLI including this one,
and the reason is the same as it is there: a bundle carries counts, and an option that let
a caller supply them from somewhere other than a run of this tree would make every
committed bundle stop being evidence that anybody ran anything.

ONE INSTANT FOR FIVE BUNDLES, WHICH IS THE ONE THING THAT READS DIFFERENTLY. Run
separately, the five bundles record five ``generated_at`` values a few minutes apart. Run
here they record one, because they now rest on one verification of one tree and five
timestamps would suggest five. Every other byte is the same, and
``--generated-at`` makes that checkable by hand: give the five commands and this one the
same instant and the bundles they write compare equal.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol

TOOLS_DIRECTORY = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIRECTORY.parent

# Both halves are forced, for the reason `tools/build_lifecycle_lambda.py` records against
# the same two lines: running this as a path puts tools/ on sys.path and not the repository
# root, while pytest puts the root on it and not tools/. The five generators are then
# imported by their bare module names, because importing them as `tools.…` makes mypy see
# one file under two module names and refuse.
for entry in (PROJECT_ROOT, TOOLS_DIRECTORY):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import build_phase0_proof
import build_phase1_proof
import build_phase2_proof
import build_phase3_proof
import build_phase5_proof

from edullm_platform.criteria import CriteriaDefinitionError
from edullm_platform.proof_bundle import GENERATOR_NESTED_ENV_VARS, ProofBundleError


class BuildBundle(Protocol):
    """The one function every generator exposes, as this tool needs to call it.

    ``verification`` is not named here even though all five accept it, and the omission is
    the point: passing one would hand a generator a measurement this tool made rather than
    one the generator made, and each phase's ``verify_repository`` scopes its targeted run
    differently. Left out, every generator verifies for itself and shares only what
    ``run_full_suite`` was already built to share.
    """

    def __call__(
        self,
        repo_root: Path,
        output_dir: Path,
        *,
        generated_at: datetime,
        regenerate_goldens: bool = False,
    ) -> tuple[Path, ...]: ...


@dataclass(frozen=True)
class Generator:
    """One generator, named by what building its bundle from here needs.

    Fields lifted off the module rather than the module itself, because a record can be
    constructed by a test and a module cannot. What a fake generator then exercises is
    this file's ordering, its error handling and its exit code, without a bundle being
    written or a pytest child being started anywhere.

    ``test_path`` is the exception: nothing here calls it. It is carried so that this
    registry can be compared with
    :data:`edullm_platform.proof_bundle.GENERATOR_TEST_PATHS`, which is the only check that
    catches the failure worth catching here -- a sixth generator added to that list and not
    to this one, whose bundle this command then leaves stale while reporting success.
    """

    phase: str
    command: str
    test_path: str
    default_output_dir: Callable[[Path], Path]
    build_bundle: BuildBundle


#: Every generator, in phase order. Held to the same set as
#: :data:`edullm_platform.proof_bundle.GENERATOR_TEST_PATHS` by
#: ``tests/test_verification_reuse.py``: a generator missing from here is one this command
#: silently does not rebuild, which is worse than not having the command, because the
#: bundle it leaves behind is stale rather than absent.
GENERATORS: Final[tuple[Generator, ...]] = tuple(
    Generator(
        phase=module.PHASE,
        command=module.GENERATOR_COMMAND,
        test_path=module.GENERATOR_TEST_PATH,
        default_output_dir=module.default_output_dir,
        build_bundle=module.build_bundle,
    )
    for module in (
        build_phase0_proof,
        build_phase1_proof,
        build_phase2_proof,
        build_phase3_proof,
        build_phase5_proof,
    )
)


@dataclass(frozen=True)
class BundleOutcome:
    """What building one phase's bundle did, whether or not it worked."""

    phase: str
    command: str
    written: tuple[Path, ...]
    error: str | None

    @property
    def built(self) -> bool:
        return self.error is None


def build_every_bundle(
    repo_root: Path,
    output_root: Path | None = None,
    *,
    generated_at: datetime,
    regenerate_goldens: bool = False,
    generators: Sequence[Generator] = GENERATORS,
) -> tuple[BundleOutcome, ...]:
    """Build each bundle in turn, and do not stop at the first one that refuses.

    A generator refuses for reasons that are about its own phase -- a drifted golden, a
    lapsed capture, a committed template the account has not caught up with -- and those
    are independent. Stopping at the first would report one of them and leave the operator
    to find the rest one regeneration at a time, at the price of a full suite run each.
    Every failure is reported together instead, and the caller decides the exit code.

    A phase that refuses writes nothing, because each generator's own refusals come before
    it writes. What a failed run leaves behind is the bundles of the phases that succeeded,
    which is exactly what running the five commands in sequence leaves behind today.
    """
    outcomes: list[BundleOutcome] = []
    for generator in generators:
        output_dir = (
            generator.default_output_dir(repo_root)
            if output_root is None
            else output_root / generator.phase
        )
        try:
            written = generator.build_bundle(
                repo_root,
                output_dir,
                generated_at=generated_at,
                regenerate_goldens=regenerate_goldens,
            )
        except (ProofBundleError, CriteriaDefinitionError) as error:
            outcomes.append(
                BundleOutcome(
                    phase=generator.phase,
                    command=generator.command,
                    written=(),
                    error=str(error),
                )
            )
            continue
        outcomes.append(
            BundleOutcome(
                phase=generator.phase,
                command=generator.command,
                written=tuple(written),
                error=None,
            )
        )
    return tuple(outcomes)


def nested_guards_that_are_set() -> tuple[str, ...]:
    """Which generators' recursion guards this process is running underneath.

    Every guard rather than one, because this command runs every generator and
    :func:`edullm_platform.proof_bundle.pytest_environment` sets all five on every nested
    run. A check on any single one would be a check on all of them today and a hole the
    day the ordering changed.
    """
    return tuple(name for name in GENERATOR_NESTED_ENV_VARS if os.environ.get(name))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build every phase's proof bundle under proof/, sharing one verification run."
        )
    )
    # --output-root rather than --output-dir, because five bundles do not have one
    # directory. It exists for the same reason the per-phase --output-dir does: writing
    # somewhere else is how a change to this machinery is shown to produce the same bytes.
    parser.add_argument("--output-root", default=None)
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--regenerate-goldens", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    nested = nested_guards_that_are_set()
    if nested:
        print(
            "refusing to build the proof bundles from inside a verification run ("
            + ", ".join(nested)
            + (" is set)" if len(nested) == 1 else " are set)"),
            file=sys.stderr,
        )
        return 2
    args = parse_args(argv)
    repo_root = PROJECT_ROOT
    output_root = None if args.output_root is None else Path(args.output_root)
    generated_at = (
        datetime.now(tz=UTC)
        if args.generated_at is None
        else datetime.fromisoformat(args.generated_at)
    )
    outcomes = build_every_bundle(
        repo_root,
        output_root,
        generated_at=generated_at,
        regenerate_goldens=args.regenerate_goldens,
    )
    for outcome in outcomes:
        for path in outcome.written:
            print(path.relative_to(repo_root) if path.is_relative_to(repo_root) else path)
    refused = [outcome for outcome in outcomes if not outcome.built]
    for outcome in refused:
        print(f"\n{outcome.phase} was not built:\n{outcome.error}", file=sys.stderr)
    if refused:
        print(
            "\n"
            + ", ".join(outcome.phase for outcome in refused)
            + " did not build. The bundles above were written; rebuild the rest with "
            + " and ".join(f"`{outcome.command}`" for outcome in refused)
            + " once the reason above is dealt with, or re-run this command.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
