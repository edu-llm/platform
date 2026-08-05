"""The three documents an agent reads, held to the tree they describe.

**WHY A TEST AT ALL, WHEN THESE ARE PROSE.** system-overview.md's agent layer states the rule
they are all built on: a skill restating a threshold in prose is wrong within a month and
wrong silently. Nothing tests prose against a threshold, so the failure is not that somebody
writes the wrong number. It is that nobody ever finds out. These tests are the finding-out.

THREE PROPERTIES, AND EACH ONE IS A THING THAT HAS ALREADY GONE WRONG SOMEWHERE IN THIS
REPOSITORY. A document naming a verb the binary does not have is `dry-run`, which every
transcript in docs-frank/working/terminal-mockups/ types. A document naming a flag the parser
does not take is `edullm shell --notebook` on a page whose options list held two flags and not
that one. A document writing out a bound is "any of the nine approvers can release it", which
was right at one gate by coincidence and wrong by seven at the other.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from edullm_platform.cli.main import (
    BUILT_TODAY,
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_REFUSED,
    EXIT_UNREACHABLE,
    EXIT_UNUSABLE,
    NOT_BUILT_YET,
    RETIRED,
    build_parser_and_verbs,
)
from tests.test_cli_no_hardcoded_bounds import WRITTEN_BOUND

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENTS = PROJECT_ROOT / "AGENTS.md"
SKILLS = PROJECT_ROOT / ".cursor" / "skills"

SUBMITTING = SKILLS / "submitting-a-run" / "SKILL.md"

#: Every document in this layer. The shared properties below are parametrized over it, so a
#: skill added later cannot arrive unheld.
AGENT_DOCUMENTS: tuple[Path, ...] = (AGENTS, SUBMITTING)

#: Every skill in this layer, which is AGENT_DOCUMENTS minus the always-on rule. Held apart
#: because the frontmatter and the length budget are properties of a skill and not of a rule.
SKILL_DOCUMENTS: tuple[Path, ...] = (SUBMITTING,)

#: What a SKILL.md body is allowed to run to. The context window is shared with the
#: conversation, the other skills and the request, so a long skill costs every turn rather
#: than the turn it is used on.
SKILL_LINE_BUDGET = 500

#: ``edullm <verb>`` wherever one of these documents writes it.
VERB_MENTION = re.compile(r"\bedullm\s+([a-z][a-z-]*)")

#: A long flag wherever one of these documents writes it.
FLAG_MENTION = re.compile(r"(?<![\w-])--[a-z][a-z0-9-]*")

#: The header of a skill's table of refusal codes, and one row of it. Scoped to that table
#: rather than to every backticked word, because the documents also name ``needs_a_dispatch``,
#: ``format_version`` and the rest of the envelope, which are keys and not codes. A rule that
#: could not tell them apart would either miss a stale code or refuse a correct key.
CODE_TABLE_HEADER = "| Code | What to do |"
CODE_TABLE_ROW = re.compile(r"^\|\s*`([a-z][a-z0-9_]*)`\s*\|")


def flags_the_parser_takes() -> set[str]:
    """Every long option on any subparser, read out of argparse's own usage lines.

    Read rather than listed, for the reason ``_nearest_flag`` gives about the same source:
    ``format_usage`` is argparse's rendering of every option a verb has, so the set cannot
    drift from the parser the way a hand-kept table would.
    """
    _, verbs = build_parser_and_verbs()
    found = {"--help", "--version"}
    for parser in verbs.values():
        found.update(re.findall(r"--[a-z][a-z0-9-]*", parser.format_usage()))
    return found


@pytest.mark.parametrize("document", AGENT_DOCUMENTS, ids=lambda path: path.name)
def test_the_document_exists_and_is_not_empty(document: Path) -> None:
    """Guards every case below: a missing file makes them all vacuously pass."""
    assert document.is_file(), f"{document} is missing"
    assert document.read_text(encoding="utf-8").strip()


@pytest.mark.parametrize("document", AGENT_DOCUMENTS, ids=lambda path: path.name)
def test_every_verb_the_document_names_is_a_verb_the_binary_knows(document: Path) -> None:
    """Mutation: write `edullm dry-run` or `edullm activity`.

    Both are real. Every transcript in docs-frank/working/terminal-mockups/ types the first
    and the second was a verb until `status` absorbed it. A retired name in an always-on rule
    is worse than one in a guide, because it is loaded into every session and an agent has no
    reason to doubt it.
    """
    known = {*BUILT_TODAY, *NOT_BUILT_YET}
    named = set(VERB_MENTION.findall(document.read_text(encoding="utf-8")))
    unknown = sorted(named - known - {"help"})

    assert not unknown, (
        f"{document.name} names verbs the binary does not have: {', '.join(unknown)}. "
        f"Retired names and what replaced them: {', '.join(sorted(RETIRED))}."
    )


@pytest.mark.parametrize("document", AGENT_DOCUMENTS, ids=lambda path: path.name)
def test_every_flag_the_document_names_is_a_flag_some_verb_takes(document: Path) -> None:
    """Mutation: write `--dry-run`, or keep `--notebook` after `shell` is built differently.

    A flag that does not exist reaches a researcher as argparse's own usage line, which names
    no flags at all because they live on the subparsers. An agent that reads one out of an
    always-on rule spends a turn discovering that.
    """
    taken = flags_the_parser_takes()
    named = set(FLAG_MENTION.findall(document.read_text(encoding="utf-8")))
    unknown = sorted(named - taken)

    assert not unknown, f"{document.name} names flags no verb takes: {', '.join(unknown)}"


@pytest.mark.parametrize("document", AGENT_DOCUMENTS, ids=lambda path: path.name)
def test_the_document_writes_no_bound_the_configuration_owns(document: Path) -> None:
    """Mutation: write "runs under an hour are released automatically".

    system-overview.md's agent layer states the rule: a skill restating a threshold in prose
    is wrong within a month and wrong silently. The same regex the CLI package is held to is
    used here, deliberately, so that the rule is one rule. `edullm check --json` prints every
    one of these numbers out of the loaded configuration, which is what a document should
    send a reader to instead of quoting.
    """
    written = [
        f"{document.name}:{number}: {line.strip()}"
        for number, line in enumerate(document.read_text(encoding="utf-8").splitlines(), 1)
        if WRITTEN_BOUND.search(line)
    ]

    assert not written, (
        "a limit, rate, count or duration is written into a document an agent reads:\n  "
        + "\n  ".join(written)
        + "\nSend the reader to edullm check --json instead, which prints it from the "
        "loaded configuration."
    )


def test_the_rule_names_every_exit_code_the_binary_can_return() -> None:
    """Mutation: document 0, 1 and 2 and leave 3 and 130 out.

    An agent branches on the exit code before it reads anything, and 3 is the only one of the
    five worth retrying. A rule that documented 2 and not 3 would produce exactly the script
    main.py's own header says was impossible before 3 existed: retry a typo forever, or never
    retry anything.
    """
    text = AGENTS.read_text(encoding="utf-8")

    for code in (EXIT_OK, EXIT_REFUSED, EXIT_UNUSABLE, EXIT_UNREACHABLE, EXIT_INTERRUPTED):
        assert re.search(rf"(?<!\d){code}(?!\d)", text), (
            f"AGENTS.md documents no exit {code}, and every path out of the binary is one "
            "of the five"
        )


def test_the_rule_names_every_built_verb() -> None:
    """Mutation: describe check and submit and leave the read-only three out.

    The rule's whole job is that an agent knows the binary exists and when to reach for it.
    A verb it does not name is a verb an agent writes a shell script for instead, which is
    the sixteen-against-nineteen problem the overview's agent layer describes.
    """
    text = AGENTS.read_text(encoding="utf-8")
    missing = sorted(verb for verb in BUILT_TODAY if f"edullm {verb}" not in text)

    assert not missing, f"AGENTS.md names no {', '.join(missing)}"


def test_the_rule_says_the_binary_holds_no_aws_credential() -> None:
    """Mutation: drop the sentence, on the grounds that it is not actionable.

    It is the most actionable sentence in the file. An agent that does not know this reaches
    for boto3 or the aws CLI the moment edullm refuses something, and for the sixteen who
    hold no AWS role that produces a confusing failure, while for the nineteen who do it
    produces an unrecorded run. Covering that is the whole point of the layer.
    """
    text = AGENTS.read_text(encoding="utf-8").lower()

    assert "aws" in text
    assert "gh" in text


def skill_frontmatter(path: Path) -> dict[str, str]:
    """The YAML block at the top of a SKILL.md, as a mapping of strings."""
    import yaml

    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path.name} has no frontmatter block"
    closing = text.index("\n---\n", 3)
    loaded = yaml.safe_load(text[4:closing])
    assert isinstance(loaded, dict)
    return {str(key): str(value) for key, value in loaded.items()}


@pytest.mark.parametrize("skill", SKILL_DOCUMENTS, ids=lambda path: path.parent.name)
def test_the_skill_declares_a_name_and_a_description(skill: Path) -> None:
    """Mutation: ship the body with no frontmatter.

    The description is what a host reads to decide whether to load the skill at all. A skill
    with none is a file nothing invokes, and a skill nothing invokes is a skill nobody has.
    """
    frontmatter = skill_frontmatter(skill)

    assert frontmatter["name"] == skill.parent.name
    assert frontmatter["description"].strip()


@pytest.mark.parametrize("skill", SKILL_DOCUMENTS, ids=lambda path: path.parent.name)
def test_the_description_says_when_to_use_it_and_not_only_what_it_does(skill: Path) -> None:
    """Mutation: write "Submits runs." and stop.

    A host matches the description against a situation. "What it does" without "when to use
    it" matches nothing, so the skill loads only when somebody names it, which is the one
    case it was not written for.
    """
    description = skill_frontmatter(skill)["description"].lower()

    assert "use when" in description or "use this when" in description


@pytest.mark.parametrize("skill", SKILL_DOCUMENTS, ids=lambda path: path.parent.name)
def test_the_skill_body_is_within_budget(skill: Path) -> None:
    """Mutation: put the whole submission path in one file.

    Every line here is paid for on every turn of every session that loads it, against a
    context window shared with the conversation and the request. Anything longer belongs in a
    reference file beside it that the agent reads when it needs to.
    """
    body = skill.read_text(encoding="utf-8").splitlines()

    assert len(body) <= SKILL_LINE_BUDGET, (
        f"{skill.parent.name} is {len(body)} lines. Move the detail into a reference file "
        "beside it and link to it once."
    )


@pytest.mark.parametrize("skill", SKILL_DOCUMENTS, ids=lambda path: path.parent.name)
def test_the_skill_reads_the_machine_form_rather_than_the_paragraphs(skill: Path) -> None:
    """Mutation: tell the agent to grep the word after "refused".

    That works until somebody rewords a refusal, and refusals get reworded constantly because
    the wording is where the remedy lives. The code is the contract and the prose is the
    courtesy, and they are not the same promise. A skill that matched on prose would be the
    reason nobody could ever improve a refusal.
    """
    text = skill.read_text(encoding="utf-8")

    assert "--json" in text


def refusal_codes_the_tree_can_produce() -> set[str]:
    """Every code a refusal can arrive under, from both halves of the vocabulary.

    Read rather than listed, which is the point. One half is the exception classes, which
    ``tests/test_refusal_codes.py`` already derives from the raise sites, and the other is the
    ``Refusal(code=...)`` literals in the CLI package, which are the checks a laptop makes
    that the compile step has no exception for. A skill matches on a code without caring
    which half it came from, so a rule about a skill must not care either.
    """
    from tests.test_refusal_codes import raise_sites

    found = {site.code for site in raise_sites() if site.code}
    for module in sorted((PROJECT_ROOT / "src" / "edullm_platform" / "cli").glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "Refusal":
                continue
            found.update(
                keyword.value.value
                for keyword in node.keywords
                if keyword.arg == "code"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
            )
    return found


def codes_the_skill_tabulates(skill: Path) -> list[str]:
    """The refusal codes in this skill's own table of them, in the order it lists them."""
    lines = skill.read_text(encoding="utf-8").splitlines()
    if CODE_TABLE_HEADER not in lines:
        return []
    rows = lines[lines.index(CODE_TABLE_HEADER) + 1 :]
    found: list[str] = []
    for line in rows:
        if not line.startswith("|"):
            break
        match = CODE_TABLE_ROW.match(line)
        if match is not None:
            found.append(match.group(1))
    return found


def test_the_vocabulary_a_skill_is_held_against_is_not_empty() -> None:
    """Guards the case below, which would otherwise pass by finding no codes to compare to.

    Mutation: break the walk, by renaming ``Refusal`` or by pointing the glob at nothing.
    Every code a skill names would then be in a set of nothing and the comparison would be
    against an empty left side, so the one rule holding these tables to the tree would go
    quiet without going red.
    """
    codes = refusal_codes_the_tree_can_produce()

    assert "unregistered_repository" in codes, "the CLI's own literals are not being read"
    assert "no_published_image" in codes, "the exception classes are not being read"


@pytest.mark.parametrize("skill", SKILL_DOCUMENTS, ids=lambda path: path.parent.name)
def test_every_refusal_code_a_skill_tabulates_is_one_the_tree_can_produce(skill: Path) -> None:
    """Mutation: rename a code in the package and leave the skill's table alone.

    THIS IS THE ONE TABLE AN AGENT ACTS ON WITHOUT CHECKING. The skills tell it to match on
    the code because the prose gets reworded, so the code is the promise, and a promise
    nothing holds is the shape this whole file exists to catch. A renamed code leaves a row
    that matches nothing, and what an agent does with a refusal it has no row for is worse
    than what it does with no table at all: it reads the detail and improvises.
    """
    tabulated = codes_the_skill_tabulates(skill)
    if not tabulated:
        pytest.skip(f"{skill.parent.name} tabulates no refusal codes")

    unknown = sorted(set(tabulated) - refusal_codes_the_tree_can_produce())

    assert not unknown, (
        f"{skill.parent.name} names refusal codes nothing raises: {', '.join(unknown)}"
    )


def test_some_skill_actually_tabulates_a_code() -> None:
    """Guards the skip above against becoming the way every skill passes.

    Mutation: reword the table header. Every skill would skip, and the case would report
    green on a layer whose tables had all gone stale.
    """
    assert any(codes_the_skill_tabulates(skill) for skill in SKILL_DOCUMENTS)
