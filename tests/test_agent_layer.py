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

#: Every document in this layer. Tasks 7 and 8 add to it, and the shared properties below are
#: parametrized over it so that a skill added later cannot arrive unheld.
AGENT_DOCUMENTS: tuple[Path, ...] = (AGENTS,)

#: ``edullm <verb>`` wherever one of these documents writes it.
VERB_MENTION = re.compile(r"\bedullm\s+([a-z][a-z-]*)")

#: A long flag wherever one of these documents writes it.
FLAG_MENTION = re.compile(r"(?<![\w-])--[a-z][a-z0-9-]*")


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
