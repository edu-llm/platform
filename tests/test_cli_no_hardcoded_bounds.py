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

**THE SECOND RULE IN THIS FILE IS THE SAME RULE AIMED AT PROSE NOBODY PRINTS.** A sweep of
every citation of ``docs-frank/reference/system-overview.md`` on 2026-08-06 found six claims
that had gone stale, and the split was clean enough to write down: every claim that rotted
restated a number, every claim that gave a reason survived, and every rotted number was
countable from the tree. So a comment may cite that document for why a set is what it is,
and may not say how many are in it. :data:`COUNTABLE` is the handful of things this tree can
count, and :func:`count_claims` holds a comment's number against the count.

Aimed at comments and docstrings, where the rule above is aimed at strings and skips
docstrings, and the two exemptions differ for a reason rather than by accident. A bound in a
docstring misleads nobody the CLI is talking to. A count in a docstring misleads the next
person to edit the file, who has no more way to check it than a researcher would.

**IT HOLDS THE NUMBER AGAINST THE TREE RATHER THAN BANNING THE NUMBER**, which is the whole
difference between this and the cheap version of it. A rule that failed on any number near a
citation would fail on the correct ones too, and the only edit that satisfies such a rule is
deleting the sentence. This one goes red exactly when the number is wrong, so the edit that
satisfies it is the edit that was wanted: name the thing in the tree and let the reader
count it. The failure message says so in as many words, because a reader told only "do not
write a number" deletes the sentence that was carrying the argument.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass
from pathlib import Path

import pytest

from edullm_platform.cli.main import BUILT_TODAY
from edullm_platform.cli.preflight import DEFERRED_TO_SUBMIT
from tools.release_paths import RELEASE_WORKFLOW, release_paths

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CLI = PROJECT_ROOT / "src" / "edullm_platform" / "cli"
CONFIG = PROJECT_ROOT / "config"

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

#: The tens, which the rule above has no use for and the count rule below cannot do without.
#: Kept out of :data:`NUMBER_WORDS` because widening that widens :data:`WRITTEN_BOUND`, and no
#: bound in ``config/policy.yaml`` is spelled above twenty. A population is: the roster carries
#: thirty-five people.
TENS = ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]

#: ``thirty-five``, spelled the way a comment spells it.
COMPOUND_WORDS = [f"{ten}-{unit}" for ten in TENS for unit in NUMBER_WORDS[:9]]

#: How much a written cardinal is worth, either spelling.
COUNT_VALUE = {word: value for value, word in enumerate(NUMBER_WORDS, 1)}
COUNT_VALUE.update({word: (index + 2) * 10 for index, word in enumerate(TENS)})

#: A cardinal somebody could mean as a count, and nothing else.
#:
#: **WHAT IT MUST NOT READ AS A COUNT IS THE HALF THAT DECIDES WHETHER THIS SURVIVES.** ``$500``
#: is money, ``v3.8.0`` is a version, ``2026`` is a year, ``8080`` is a port and ``#351`` is a
#: pull request. Every one of them is excluded by what may stand in front of the digits or
#: behind them rather than by a list of the places they turn up, for the reason the docstring
#: gives about exemption lists. Three digits at most, because the largest thing this file
#: counts is the roster and a four-digit number in this tree is a year or a port.
#:
#: The compound spellings are tried first and the lookbehind refuses a cardinal after a hyphen,
#: which is two guards against the same defect: ``thirty-five members`` read as ``five`` would
#: report a correct sentence as wrong by thirty. A guard that cries wolf gets routed around
#: rather than fixed -- one in this repository was, three hours after it fired, by an author
#: who pushed hex letters into an identifier so a digits-only pattern would stop matching.
COUNT = (
    r"(?<![$\w.#/-])"
    r"(?:\d{1,3}(?![\d.%])"
    r"|(?i:" + "|".join([*COMPOUND_WORDS, *TENS, *NUMBER_WORDS]) + r")(?![\w-])"
    r")"
)


@dataclass(frozen=True)
class Countable:
    """One thing the tree can count, and the wording a comment counts it by.

    ``phrase`` is deliberately narrow and every alternative in it is a wording this tree has
    actually used. A phrase written from imagination matches the sentences somebody would
    write on purpose rather than the ones they wrote by accident, and a wide one collides
    with a correct sentence about something else: ``verbs`` on its own matches "the two verbs
    that start an instance" and "the three verbs that resolve an id", both true and neither
    about this set.
    """

    #: What the failure calls it.
    named: str
    #: The wording, as a regex alternation, that a number in front of means this set.
    phrase: str
    #: How many there are, read out of the tree at collection.
    count: int
    #: Where a comment should point instead of writing the number.
    tree: str


def countables() -> tuple[Countable, ...]:
    """The handful, counted from the tree rather than remembered.

    **THE ROSTER IS NOT IN HERE AND THE REASON IS EVIDENCE RATHER THAN TASTE.** People in
    ``config/organization.yaml`` is the fourth countable the sweep named, and no wording keys
    it: that file's own header writes "sixteen of the thirty-five roster members" and
    "Fifteen roster members still hold no role" within two paragraphs, both correct and both
    about subsets. A rule keyed on the noun cannot tell a subset from the whole, and the
    version that could would need the exemption list this module refuses to grow.
    """
    return (
        Countable(
            named="the configuration files that are the control plane",
            phrase=r"control-plane files|configuration files (?:as|that are) the control plane",
            count=len(sorted(CONFIG.glob("*.yaml"))),
            tree="config/*.yaml",
        ),
        Countable(
            named="the paths under config/ the release trigger names",
            phrase=r"(?:files|paths) under `?config/`?",
            count=len(
                [
                    path
                    for path in release_paths(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
                    if path.startswith("config/")
                ]
            ),
            tree="tools/release_paths.py, which parses the trigger",
        ),
        Countable(
            named="the verbs the binary has",
            phrase=r"verbs? that work|verbs?,? all built|built verbs?",
            count=len(BUILT_TODAY),
            tree="src/edullm_platform/cli/main.py::BUILT_TODAY",
        ),
        Countable(
            named="the checks a laptop cannot make",
            phrase=r"checks? deferred|deferred checks?",
            count=len(DEFERRED_TO_SUBMIT),
            tree="src/edullm_platform/cli/preflight.py::DEFERRED_TO_SUBMIT",
        ),
    )


COUNTABLE: tuple[Countable, ...] = countables()


def value_of(written: str) -> int:
    """A cardinal spelled either way, as the number it is."""
    text = written.replace(",", "").lower()
    if text.isdigit():
        return int(text)
    ten, _, unit = text.partition("-")
    return COUNT_VALUE[ten] + (COUNT_VALUE[unit] if unit else 0)


def written_prose(source: str) -> list[tuple[int, str]]:
    """Every comment and every docstring, with adjacent comment lines joined into one run.

    Joined because these files wrap at a hundred characters and the number is routinely on
    the line above the noun it counts. ``main.py`` writes "a list of five, read by somebody
    choosing a verb" across two of them, and a line-by-line reader sees no claim at all.
    """
    runs: list[tuple[int, str]] = []
    run: list[str] = []
    opened = previous = 0
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        if run and token.start[0] != previous + 1:
            runs.append((opened, " ".join(run)))
            run = []
        if not run:
            opened = token.start[0]
        run.append(token.string.lstrip("#").lstrip(": ").rstrip())
        previous = token.start[0]
    if run:
        runs.append((opened, " ".join(run)))
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            written = ast.get_docstring(node, clean=False)
            if written and node.body:
                runs.append((node.body[0].lineno, " ".join(written.split())))
    return runs


def count_claims(prose: list[tuple[int, str]]) -> list[tuple[int, str, Countable]]:
    """Every place a number is written against something this tree counts, and disagrees."""
    found = []
    for countable in COUNTABLE:
        rule = re.compile(rf"({COUNT})\s+(?:{countable.phrase})", re.IGNORECASE)
        for line, text in prose:
            found += [
                (line, match.group(0), countable)
                for match in rule.finditer(text)
                if value_of(match.group(1)) != countable.count
            ]
    return sorted(found)


def point_it_at_the_tree(written: list[tuple[int, str, Countable]], *, where: str) -> str:
    """The failure, which has to leave a reader somewhere better than deleting the sentence."""
    return (
        f"{where} writes down a count the tree already carries, and the tree has moved:\n  "
        + "\n  ".join(
            f"{line}: {said!r} -- there are {countable.count} "
            f"({countable.named}, in {countable.tree})"
            for line, said, countable in written
        )
        + "\n\nDo not delete the sentence, and do not reword the number out of it. Cite "
        "docs-frank/reference/system-overview.md for why the set is what it is, and name the "
        "thing in the tree instead of saying how many are in it. 'every verb in BUILT_TODAY' "
        "is still true the day a tenth one lands; 'the nine verbs' is wrong that day and "
        "silent about it."
    )


def cli_modules() -> list[Path]:
    return sorted(CLI.glob("*.py"))


def source_modules() -> list[Path]:
    """Every module whose comments this reads.

    Python for now and not the YAML headers under ``config/reports/`` or ``infra/``, which
    carry the other four of the sweep's six and are the files an open pull request is
    rewriting this week. Widening is this tuple, not a code change.
    """
    return sorted(
        path
        for root in ("src", "tools", "tests")
        for path in (PROJECT_ROOT / root).rglob("*.py")
    )


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


def test_no_comment_restates_a_count_the_tree_owns() -> None:
    """Mutation: write the size of ``BUILT_TODAY`` into the comment above it, as main.py did.

    One test over every module rather than one per module, because the useful failure is the
    whole list. Somebody correcting a count that moved is going to want every place it was
    written, and finding them one red test at a time is how the second copy gets missed.

    **THIS FILE IS IN ITS OWN REACH AND THAT IS NOT AN OVERSIGHT.** The stale wordings this
    rule exists to catch have to be written down somewhere to be tested against, and the case
    below writes them as string literals for exactly this reason. A test module that could
    quote a wrong count in its own prose would be the one file in the tree allowed to carry
    one, which is where it would then sit.
    """
    written = [
        (f"{path.relative_to(PROJECT_ROOT)}:{line}", said, countable)
        for path in source_modules()
        for line, said, countable in count_claims(
            written_prose(path.read_text(encoding="utf-8"))
        )
    ]

    assert not written, point_it_at_the_tree(written, where="a comment or a docstring")


def test_the_count_rule_can_see_a_count_that_has_moved() -> None:
    """The tripwire's own tripwire, because a phrase that matches nothing passes everything.

    Every string here is a wording this tree used. The first two are what ``main.py`` said
    about ``BUILT_TODAY`` in a comment and in a docstring, and the last two are what four
    ``config/reports/`` headers said about the control plane and about the release trigger
    before 2026-08-06.
    """
    claims = count_claims(
        [
            (1, "The five verbs that work, and the line each shows in --help."),
            (2, "Ten verbs, all built."),
            (3, "system-overview.md names eight configuration files as the control plane"),
            (4, "release-tag.yml names six files under config/ and no directory"),
            (5, "It is one check deferred and not more."),
        ]
    )

    said = {line: text for line, text, _ in claims}
    assert 1 in said, "the rule cannot see the count it was written for"
    # THE CONTROL, AND IT HAS TO BE MOVED EVERY TIME A VERB LANDS. It said nine until
    # ``edullm stop`` made it ten, and being made to edit it is the point rather than the
    # friction: a control sentence that stayed correct through a change of the count would be
    # asserting that the rule ignores this phrase, which is the failure it exists to rule out.
    assert 2 not in said, "the current count is the count, and a correct sentence must pass"
    assert set(said) == {1, 3, 4, 5}, said


def test_the_count_rule_leaves_a_number_that_is_not_a_count_alone() -> None:
    """The half that decides whether this survives contact with the tree.

    A rule that reads a version, a port, a year or a price as a count fires on correct
    prose, and a guard that fires on correct prose gets worked around rather than fixed.
    The last two are the ones a bare noun would have caught: both are true, both are about
    a set that is not this one, and both are real text from this repository.
    """
    for harmless in (
        "install v3.8.0, whose built verbs are the ones the tag carries",
        "$500 checks deferred is not a sentence, but the rule must not read the money",
        "since 2026 the built verbs have grown",
        "the health check runs on port 8080; deferred checks are a different thing",
        "#351 landed the ninth of the configuration files as the control plane",
        "run and shell are the two verbs that start an instance without a price",
        "caught here rather than in each of the three verbs that resolve an id",
    ):
        assert not count_claims([(1, harmless)]), harmless


def test_every_countable_can_actually_be_counted() -> None:
    """Guards the table: a countable that reads zero would pass every sentence about it.

    The way that happens is a rename. ``config/*.yaml`` counted by glob is zero the day the
    directory moves, and a rule asserting that a comment saying nine means zero would go red
    everywhere, which is at least loud. A rule whose phrase stopped matching goes quiet
    instead, so the phrases are exercised above rather than only declared here.
    """
    for countable in COUNTABLE:
        assert countable.count > 0, f"{countable.named} counts nothing, so it holds nothing"
        assert countable.tree, countable.named
    assert len(COUNTABLE) == len({countable.phrase for countable in COUNTABLE})


def test_the_modules_this_reads_are_the_ones_it_claims_to() -> None:
    """Guards the glob, for the reason the case above guards the counts."""
    names = {path.relative_to(PROJECT_ROOT).as_posix() for path in source_modules()}

    assert "src/edullm_platform/cli/main.py" in names
    assert "tools/release_paths.py" in names
    assert "tests/test_cli_no_hardcoded_bounds.py" in names
