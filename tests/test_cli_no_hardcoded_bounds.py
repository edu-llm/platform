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

**A NUMBER SPELLED AS AN ENGLISH WORD IS A NUMBER, AND FOR A YEAR THIS COULD NOT SEE ONE.**
The rule looked for digits, so ``"any of the nine approvers can release it"`` passed it
twice over -- in ``main.py`` after every non-automatic submit and in ``presentation.py``
as a routine-run fallback. Nine was the size of the union of ``admins`` and ``team_leads``
on the day it was typed and was never true of ``run-approval-admin``, where two people can
release, so the sentence was wrong by seven for exactly the runs that cost the most. A
count is the one kind of bound a person reaches for a word to write, which made the word
form the likeliest spelling of the mistake and the one spelling nothing checked.

Docstrings are exempt and only docstrings. They are not read by anybody the CLI is talking
to, and the two that quote a figure are quoting a mockup's history rather than asserting a
current limit -- a sentence about what a transcript used to print stays true when the
configuration changes.

**AND THERE IS NO EXEMPTION LIST, WHICH IS WHAT DECIDES THE COLLISIONS.** The docstring
above already argues that an exemption list is where the next hardcoded ceiling hides, so
a sentence that reads as a bound and is not one gets reworded rather than excused. The word
half cost exactly one of those, in the unregistered-repository refusal, and it was reworded
rather than exempted. That refusal names ``edullm add repository`` now and says nothing
about a count.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

CLI = Path(__file__).resolve().parent.parent / "src" / "edullm_platform" / "cli"

#: The English words a count reaches a terminal spelled as. Stops at twenty, which is past
#: every population ``config/organization.yaml`` holds and past every bound in
#: ``config/policy.yaml``. ``no``, ``none`` and ``zero`` are deliberately absent: they are
#: ordinary English here far more often than they are a count -- "no refusals", "none at
#: all", "no dispatch of submit-run.yml" -- and a bound of nothing is not one anybody
#: drifts from.
NUMBER_WORDS = [
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
    "twenty",
]

#: A number written either way, so that every rule below reads a word exactly as it reads a
#: digit. The case fold is scoped to the words with ``(?i:...)`` rather than set on the
#: whole pattern, because the unit spellings are exact on purpose: ``24h`` is a duration and
#: ``24H`` is not something written here, and ``GiB`` is not ``gib``.
#:
#: The optional multiplier is what reaches the money bounds. ``routine_maximum_cost_usd`` is
#: 500 and the words alone stop at twenty, so without it the one threshold most worth
#: catching would be the one spelling that slipped through.
NUMBER = (
    r"(?:\b\d[\d,]*(\.\d+)?"
    r"|\b(?i:" + "|".join(NUMBER_WORDS) + r")\b(?:[ -](?i:hundred|thousand)\b)?)"
)

#: A figure a reader would take as a limit. Money in any form, a duration or size with a
#: unit attached, and a count of the people a gate can ask. Deliberately not "any number":
#: ``line[3:]`` and ``width=84`` are not bounds, ``one run`` and ``two clients`` are not
#: populations, and a rule that flags them gets an exemption list -- which is where the next
#: hardcoded ceiling would hide.
#:
#: The last rule is an inclusion list rather than an exclusion one, and that is the same
#: argument the other way round. Who may release at a gate is decided by ``admins`` and
#: ``team_leads`` in ``config/organization.yaml`` and by nothing else, so those four nouns
#: are exactly the ones whose count this package must never write down.
WRITTEN_BOUND = re.compile(
    rf"""
    \$\s*{NUMBER}                            # $500, $ 0.526, five dollars
    | {NUMBER}\s*(USD|usd|dollars?)          # 500 USD
    | {NUMBER}\s*(h|hr|hrs)\b                # 24h
    | {NUMBER}[ -](hour|hours|minute|minutes|day|days)\b
    | {NUMBER}\s*(GB|GiB|TB)\b               # 96 GB
    | {NUMBER}[ -](approver|reviewer|lead|admin)s?\b   # nine approvers, 2 admins
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


def test_the_rule_can_see_a_bound_spelled_as_a_word() -> None:
    """The half that shipped the defect, and the ordinary English it has to leave alone.

    Mutation: read digits only. That is what the rule did, and both copies of "any of the
    nine approvers can release it" went out under it -- a count that was right for one gate
    by coincidence, wrong for the other by seven, and invisible to the one check whose whole
    job is to stop a policy number being written down.
    """
    assert WRITTEN_BOUND.search('"any of the nine approvers can release it"')
    assert WRITTEN_BOUND.search('"waiting at run-approval-admin. Any of the two admins"')
    assert WRITTEN_BOUND.search('"routine work is bounded at twenty-four hours"')
    assert WRITTEN_BOUND.search('"it gives up after eleven minutes"')
    assert WRITTEN_BOUND.search('"a ceiling of five hundred dollars"')
    # A number word that counts nothing this configuration decides. Every one of these is
    # real text from the package, and a rule that flagged them would earn an exemption list.
    assert not WRITTEN_BOUND.search('"no refusals. edullm submit will dispatch this."')
    assert not WRITTEN_BOUND.search('"One machine, two clients -- an editor or Jupyter"')
    assert not WRITTEN_BOUND.search('"one run; omit for your recent submissions"')
    assert not WRITTEN_BOUND.search('"--hours takes a positive base-ten number of hours"')
    assert not WRITTEN_BOUND.search('"an id given in full is found in the first one or two"')


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
