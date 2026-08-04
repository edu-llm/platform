"""The tripwire that keeps every bound in the configuration and nowhere else.

THIS IS A STRUCTURAL RULE RATHER THAN A REVIEW HABIT, AND THE DIFFERENCE IS THE POINT. A
number copied into a refusal is correct on the day it is typed, which is exactly why nobody
catches it later: the copy and the original agree, the review passes, and the drift happens
months afterwards when somebody edits the file that is the limit and has no reason to think
a string in ``main.py`` also says so. The automatic runtime bound has already disagreed
between the documents and ``config/policy.yaml`` three separate times by that route.

So rather than asking the two copies to agree, this asks that there be one. Any money or
duration that reaches a terminal has to be interpolated -- ``f"${plain_decimal(ceiling)}"``
rather than ``"$500"`` -- and interpolation is what this can see: an f-string's literal
parts are ``"$"`` and ``"/hour"``, which carry no digits, while a written-out figure does.

Docstrings are exempt and only docstrings. They are not read by anybody the CLI is talking
to, and the two that quote a figure are quoting a mockup's history rather than asserting a
current limit -- a sentence about what a transcript used to print stays true when the
configuration changes.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

CLI = Path(__file__).resolve().parent.parent / "src" / "edullm_platform" / "cli"

#: A figure a reader would take as a limit. Money in any form, and a duration with a unit
#: attached. Deliberately not "any digit": ``line[3:]`` and ``width=84`` are not bounds, and
#: a rule that flags them gets an exemption list, and an exemption list is where the next
#: hardcoded ceiling would hide.
WRITTEN_BOUND = re.compile(
    r"""
    \$\s*\d                                  # $500, $ 0.526
    | \b\d[\d,]*(\.\d+)?\s*(USD|usd|dollars) # 500 USD
    | \b\d[\d,]*(\.\d+)?\s*(h|hr|hrs)\b      # 24h
    | \b\d[\d,]*(\.\d+)?[ -](hour|hours|minute|minutes|day|days)\b
    | \b\d[\d,]*(\.\d+)?\s*(GB|GiB|TB)\b     # 96 GB
    """,
    re.VERBOSE,
)


def cli_modules() -> list[Path]:
    return sorted(CLI.glob("*.py"))


def spoken_strings(source: str) -> list[tuple[int, str]]:
    """Every string literal the CLI could print, which is all of them but the docstrings."""
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_the_rule_can_see_a_bound_that_was_written_out() -> None:
    """The tripwire's own tripwire, because a regex that matches nothing passes everything."""
    assert WRITTEN_BOUND.search('"a ceiling of $500 a run"')
    assert WRITTEN_BOUND.search('"routine work is bounded at 1h"')
    assert WRITTEN_BOUND.search('"anything over 4 hours needs releasing"')
    assert WRITTEN_BOUND.search('"$0.526 an hour against $55.04"')
    assert WRITTEN_BOUND.search('"4 x A10G 96 GB"')
    # And what interpolation leaves behind, which is what the CLI is required to do instead.
    assert not WRITTEN_BOUND.search('"$"')
    assert not WRITTEN_BOUND.search('"/hour x "')
    assert not WRITTEN_BOUND.search('" a run"')


@pytest.mark.parametrize("module", cli_modules(), ids=lambda path: path.name)
def test_no_bound_is_written_into_a_string_the_cli_prints(module: Path) -> None:
    written = [
        f"{module.name}:{line}: {text!r}"
        for line, text in spoken_strings(module.read_text())
        if WRITTEN_BOUND.search(text)
    ]
    assert not written, (
        "a limit, rate or size is written out rather than read from the configuration:\n  "
        + "\n  ".join(written)
        + "\nInterpolate it from the loaded configuration at the point of printing."
    )


def test_the_package_is_actually_being_read() -> None:
    """Guards the glob: a moved package would make every case above vacuously pass."""
    names = {path.name for path in cli_modules()}
    assert {"main.py", "presentation.py", "preflight.py"} <= names
