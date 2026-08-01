"""Every argument a workflow hands a tool, checked against the tool's own parser.

THIS EXISTS BECAUSE THE GAP IT CLOSES COST A DISPATCH. ``--policy`` was added to
``tools/resolve_published_image.py`` as a required argument, every caller in the test suite
was updated, the suite went green, and the one caller no test runs -- the workflow -- was
not. The next submission failed in the resolve job with argparse's usage message, after the
run had assumed a role and before anything was compiled.

The suite could not have caught it. ``test_the_tools_the_run_bodies_reach_for_exist_on_disk``
asserts the file is there; nothing asserted the call would parse. A workflow is the only
caller of these tools that no unit test invokes, which makes it the only one where a
signature change fails in production rather than in CI.

**Read from argparse rather than from a list.** A test naming the expected flags would be a
third place to update and would go stale the same way. These build each tool's parser and
ask it, so a flag renamed in the tool and not in the workflow fails here, and so does a
required argument added in the tool and not passed by the workflow -- which is the direction
that actually happened.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = PROJECT_ROOT / ".github" / "workflows"

#: Matches ``uv run --frozen python tools/<name>.py`` and everything up to the end of its
#: line continuations, which is how every one of these is invoked.
#: The continuation branch is first on purpose. With ``[^\n]`` first, the alternation matches
#: the backslash, stops at the newline it was meant to consume, and every argument on a
#: continued line goes unseen -- which reports the workflow as omitting arguments it passes.
INVOCATION = re.compile(
    r"python\s+(tools/[A-Za-z0-9_]+\.py)((?:\\\n|[^\n])*)",
)


def run_bodies() -> list[tuple[str, str]]:
    """Every ``run:`` script in every workflow, with the file it came from."""
    bodies: list[tuple[str, str]] = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            continue
        for job in loaded.get("jobs", {}).values():
            for step in job.get("steps", []) or []:
                script = step.get("run")
                if isinstance(script, str):
                    bodies.append((path.name, script))
    return bodies


def parser_for(tool: str) -> argparse.ArgumentParser | None:
    """The tool's own parser, by importing it rather than by reading its source.

    Every one of these modules guards its work behind ``if __name__ == "__main__"``, so
    importing builds the parser and runs nothing.

    ``None`` for a tool that has no ``build_parser``, which is not a defect: the phase gate
    shims and the schema exporter take no arguments at all. It reads as an empty parser, so
    such a tool requires nothing and accepts nothing -- and a workflow that passed it a flag
    still fails, on the second test rather than the first.
    """
    path = PROJECT_ROOT / tool
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before execution, for the reason `tests/gate_support.py` gives at its own
    # loader: `@dataclass` resolves a string annotation by looking the defining module up in
    # sys.modules, and a module built from a file path is not there unless it is put there.
    # This loader went years without it because no tool a workflow runs held a dataclass.
    # The first one that did failed with an AttributeError raised inside dataclasses.py,
    # naming neither this file nor the tool, and only under a run that had not already
    # imported the tool by some other route -- so it passed locally and failed in CI.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    build = getattr(module, "build_parser", None)
    if not callable(build):
        return None
    parser: argparse.ArgumentParser = build()
    return parser


def flags_passed(invocation: str) -> set[str]:
    return set(re.findall(r"(?<![-\w])(--[a-z][a-z0-9-]*)", invocation))


def invocations() -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    for workflow, body in run_bodies():
        for tool, arguments in INVOCATION.findall(body):
            found.append((workflow, tool, arguments))
    return found


def required_flags(parser: argparse.ArgumentParser | None) -> set[str]:
    if parser is None:
        return set()
    return {
        option
        for action in parser._actions
        if action.required
        for option in action.option_strings
        if option.startswith("--")
    }


def known_flags(parser: argparse.ArgumentParser | None) -> set[str]:
    if parser is None:
        return set()
    return {
        option
        for action in parser._actions
        for option in action.option_strings
        if option.startswith("--")
    }


def test_the_workflows_call_at_least_one_tool_so_this_module_is_not_vacuous() -> None:
    """A regex that matched nothing would make every test below pass.

    The count is not pinned, because a new invocation should not have to be counted in --
    it should have to parse, which is what the tests below do.
    """
    assert invocations()


@pytest.mark.parametrize(
    ("workflow", "tool", "arguments"),
    [pytest.param(*found, id=f"{found[0]}:{found[1]}") for found in invocations()],
)
def test_a_workflow_passes_every_argument_its_tool_requires(
    workflow: str, tool: str, arguments: str
) -> None:
    """Mutation: add a required argument to a tool and update only its tests.

    That is what happened. The tool's callers in the suite were all updated, the suite was
    green, and the workflow -- which no test runs -- failed on the next dispatch with
    argparse's usage message, inside a job that had already assumed a role.
    """
    parser = parser_for(tool)
    missing = required_flags(parser) - flags_passed(arguments)

    assert not missing, (
        f"{workflow} calls {tool} without {sorted(missing)}, which its parser requires; "
        "the workflow fails at argparse on the next dispatch and no other test sees it"
    )


@pytest.mark.parametrize(
    ("workflow", "tool", "arguments"),
    [pytest.param(*found, id=f"{found[0]}:{found[1]}") for found in invocations()],
)
def test_a_workflow_passes_no_argument_its_tool_does_not_accept(
    workflow: str, tool: str, arguments: str
) -> None:
    """The other direction. Mutation: rename a flag in the tool and not in the workflow.

    Fails the same way and reads the same way, and is the likelier of the two to be done by
    somebody tidying an interface rather than changing one.
    """
    unknown = flags_passed(arguments) - known_flags(parser_for(tool))

    assert not unknown, (
        f"{workflow} passes {sorted(unknown)} to {tool}, which its parser does not accept"
    )
