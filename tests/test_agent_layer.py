"""The documents an agent reads, held to the tree they describe.

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

**THE LAYER IS ONE SKILL NOW, AND THE AUDIENCE IS WHY.** There were three: two under
`.cursor/` and one shipped. The two local ones were the platform protocol addressed to
somebody working in this checkout, and nobody working in this checkout is a researcher --
this repository is the maintainers', who deploy the infrastructure, hold AWS sessions and run
the boto3 tools that those documents told their reader never to reach for. So they said the
opposite of what was true here while duplicating, for an audience of nobody, a file that
already existed for the audience it was written for. They were deleted rather than corrected,
and every case that was pointed at one now reads the shipped skill.
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
from tests.test_cli_no_hardcoded_bounds import (
    WRITTEN_BOUND,
    count_claims,
    point_it_at_the_tree,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: The always-on rule for this checkout, which is a maintainer's. It describes the tree, the
#: checks and the habits that have cost somebody an afternoon, and it is held below only to
#: the binary's vocabulary -- a verb it names has to exist, a flag it names has to be taken,
#: and a bound the configuration owns may not be written into it.
AGENTS = PROJECT_ROOT / "AGENTS.md"

#: THE ONE DOCUMENT IN THIS LAYER THAT LEAVES THE REPOSITORY, AND THAT IS WHAT IT IS HELD
#: HARDEST TO. A researcher's agent works in OLMo-core or in a codebase of its own, never has
#: this checkout, and so reads nothing else here. This is copied into their tree instead,
#: which is why it is committed outside ``.cursor/`` and why it may lean on nothing else.
RESEARCHER_SKILL = PROJECT_ROOT / "skills" / "edullm-platform" / "SKILL.md"
SKILLS_README = PROJECT_ROOT / "skills" / "README.md"

#: Every document in this layer. The shared properties below are parametrized over it, so a
#: skill added later cannot arrive unheld.
AGENT_DOCUMENTS: tuple[Path, ...] = (AGENTS, RESEARCHER_SKILL, SKILLS_README)

#: Every skill in this layer, which is AGENT_DOCUMENTS minus the always-on rule and the page
#: that says where to put one. Held apart because the frontmatter and the length budget are
#: properties of a skill and not of a rule. A tuple of one, and a tuple rather than a bare
#: path so that a second shipped skill arrives already held.
SKILL_DOCUMENTS: tuple[Path, ...] = (RESEARCHER_SKILL,)

#: What a SKILL.md body is allowed to run to. The context window is shared with the
#: conversation, the other skills and the request, so a long skill costs every turn rather
#: than the turn it is used on.
SKILL_LINE_BUDGET = 500

#: ``edullm <verb>`` wherever one of these documents writes it.
#:
#: The lookbehind is what stops ``.edullm`` being read as the binary. ``.edullm`` is the spec
#: directory and these documents talk about it constantly, so "no `.edullm` directory" would
#: otherwise report ``directory`` as a verb the binary does not have. A rule that cried wolf
#: on a correct sentence would get reworded around rather than fixed, and the next reword
#: would be the one hiding a real retired name.
VERB_MENTION = re.compile(r"(?<![.\w-])edullm\s+([a-z][a-z-]*)")

#: A long flag wherever one of these documents writes it.
FLAG_MENTION = re.compile(r"(?<![\w-])--[a-z][a-z0-9-]*")

#: A span a reader would copy and run: one inline code span, or one line inside a fence.
INLINE_SPAN = re.compile(r"`([^`\n]+)`")

#: Where one shell command ends and the next begins inside such a span.
BETWEEN_COMMANDS = re.compile(r"\|\||&&|[|;]")

#: A sentence claiming something is not built. Every wording the tree has actually used is in
#: here, which is the only reason to trust it: a pattern written from imagination matches the
#: sentences somebody would write on purpose rather than the ones they wrote by accident.
#: ``AGENTS.md`` said "are settled and not built" and
#: ``docs-frank/working/what-you-can-test-tonight.md`` said 'print "not built yet"'.
UNBUILT_CLAIM = re.compile(
    r"\b(?:not\s+built|unbuilt|not\s+yet\s+built|does\s+not\s+exist\s+yet)\b", re.IGNORECASE
)

#: A verb named anywhere in a sentence, whether or not the binary's name is in front of it.
#: Wider than :data:`VERB_MENTION` on purpose: the false sentence in
#: ``what-you-can-test-tonight.md`` named its four as bare backticked words -- "`run`, `shell`,
#: `add`, `ask`" -- with the one ``edullm`` two lines earlier, so a pattern requiring the
#: binary's name would have read straight past the thing it exists to catch.
NAMED_VERB = re.compile(r"`(?:edullm\s+)?([a-z][a-z-]*)`|(?<![.\w-])edullm\s+([a-z][a-z-]*)")

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


def fenced_lines(text: str) -> list[str]:
    """Every line inside a fenced block, which is what a reader copies rather than reads."""
    lines: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("```"):
            inside = not inside
            continue
        if inside:
            lines.append(line)
    return lines


def flags_of_other_programs(text: str) -> set[str]:
    """Long options that belong to some command other than the one this layer documents.

    **THESE DOCUMENTS TELL PEOPLE TO RUN THINGS THAT ARE NOT ``edullm``, AND UNTIL AGENTS.md
    WAS REWRITTEN FOR ITS ACTUAL AUDIENCE THEY BARELY DID.** The shipped skill installs with
    ``uv`` and sets a variable with ``gh``; the maintainer rule runs ``uv sync --locked``,
    ``pytest --dist loadgroup`` and ``ruff format --check``. Every one of those is a real
    instruction a reader follows, and none of them is argparse's to answer for. The case
    below asks whether a flag written against *this* binary exists, so somebody else's has to
    come out first or the file's only options are to be wrong or to be useless.

    Subtracted by reading who the command is addressed to rather than by listing the tools,
    which would be a list that rots the first time a document mentions a fourth one. A span
    naming ``edullm`` anywhere is kept, so ``uv run edullm check --json`` goes on being
    checked -- the program is ``uv`` and the flag is this binary's.

    A bare flag in prose -- "Pass ``--experiment``" -- names no program and is kept, which is
    the half worth stating: it is most of the flags these documents write, and scoping this
    to fenced blocks or to sentences mentioning the binary would have quietly stopped
    checking the refusal table, where a stale flag costs the most.

    THE CEILING, SO THAT NOBODY READS THIS AS MORE THAN IT IS: what comes back is a set of
    names, so a flag excused once in a document is excused everywhere in that document. A
    document writing ``gh pr create --draft`` and then an invented ``edullm submit --draft``
    would get away with the second. That is narrow enough to accept and too quiet to leave
    unwritten.
    """
    found: set[str] = set()
    spans = [*fenced_lines(text), *INLINE_SPAN.findall(text)]
    for span in spans:
        for segment in BETWEEN_COMMANDS.split(span):
            words = segment.split()
            if not words or words[0].startswith("-") or "edullm" in segment:
                continue
            found.update(FLAG_MENTION.findall(segment))
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
    text = document.read_text(encoding="utf-8")
    named = set(FLAG_MENTION.findall(text))
    unknown = sorted(named - flags_the_parser_takes() - flags_of_other_programs(text))

    assert not unknown, f"{document.name} names flags no verb takes: {', '.join(unknown)}"


def test_another_programs_flag_is_excused_and_this_binarys_invented_one_is_not() -> None:
    """Guards the subtraction above, which is the half of that case that can rot silently.

    Mutation: excuse every flag inside any code span, rather than only those in a command
    addressed elsewhere. `edullm check --dry-run` is written in a fence in every transcript
    in docs-frank/working/terminal-mockups/, so the widest plausible version of this rule is
    also the one that stops catching the exact mistake the layer has actually made.

    Four spans, and each is a shape that appears in a document above: somebody else's command
    with its own flag, that same command with this binary's name inside it, a bare flag in
    prose with no program at all, and an invented flag on this binary.
    """
    document = (
        "Run `uv run pytest -q --dist loadgroup`, then `uv run edullm check --json`.\n"
        "Pass `--experiment` with a slug, and never `--dry-run`.\n"
    )

    excused = flags_of_other_programs(document)
    named = set(FLAG_MENTION.findall(document))

    assert "--dist" in excused, "pytest's own flag is being held against this binary"
    assert "--json" not in excused, "a span naming edullm is not this binary's to answer for"
    assert "--experiment" not in excused, "a bare flag in prose names no other program"
    assert sorted(named - flags_the_parser_takes() - excused) == ["--dry-run"], (
        "the subtraction no longer leaves an invented flag on this binary visible"
    )


def sentences(text: str) -> list[str]:
    """The document as sentences, with the line wrapping taken out.

    Sentences rather than lines, because prose in these files is wrapped at ninety-odd
    characters and the subject of a claim is routinely on the line above the claim. Reading
    line by line would have missed the exact sentence this exists for: "`edullm run` and
    `edullm shell` are settled and not built" fits on one line, and the four-verb version in
    the local document does not.
    """
    return re.split(r"(?<=[.!?])\s+", " ".join(text.split()))


def verbs_named_in(sentence: str) -> set[str]:
    return {
        found
        for pair in NAMED_VERB.findall(sentence)
        for found in pair
        if found
    }


def claims_about_what_is_built(text: str) -> list[tuple[str, set[str]]]:
    """Every sentence saying something is not built, and the verbs it says it about."""
    return [
        (sentence, verbs_named_in(sentence))
        for sentence in sentences(text)
        if UNBUILT_CLAIM.search(sentence)
    ]


@pytest.mark.parametrize("document", AGENT_DOCUMENTS, ids=lambda path: path.name)
def test_no_document_says_a_built_verb_is_not_built(document: Path) -> None:
    """**Mutation: write "`edullm run` and `edullm shell` are settled and not built".**

    THAT SENTENCE WAS IN AGENTS.md AND IT WAS FALSE FOR AS LONG AS THE TWO VERBS HAVE
    EXISTED. ``NOT_BUILT_YET`` is an empty dictionary and both are in ``BUILT_TODAY``, so the
    binary they describe had nine working verbs while the always-on rule described seven and a
    plan. This file is loaded into every agent session in the organization, so it was not one
    stale sentence: it was every assistant in the org being told, with no reason to doubt it,
    that the two newest verbs did not work. An agent told a verb is unbuilt does not run it to
    find out.

    THE THREE CASES ABOVE COULD NOT HAVE CAUGHT IT, WHICH IS WHY THIS IS A FOURTH RATHER THAN
    A WIDENING. Each of them asks whether a name the document uses is a name the binary knows,
    and `run` and `shell` are names the binary knows. Nothing compared what a document
    *asserts about* a verb with what the tables say. That is the general shape: a document is
    held to the vocabulary and not to the claims, and a false claim in the right vocabulary
    passes every check in the file.

    Driven off both tables rather than off a list of the two verbs that were wrong tonight, so
    it keeps working in the other direction too: when something is genuinely unbuilt again,
    ``NOT_BUILT_YET`` is where it is declared and a document may say so freely. The case below
    proves that half, since the table is empty today and this one would otherwise be asserting
    against nothing.
    """
    wrong = [
        (sentence, sorted(named & set(BUILT_TODAY)))
        for sentence, named in claims_about_what_is_built(document.read_text(encoding="utf-8"))
        if named & set(BUILT_TODAY)
    ]

    assert not wrong, (
        f"{document.name} says a built verb is not built:\n  "
        + "\n  ".join(f"{', '.join(verbs)}: {sentence}" for sentence, verbs in wrong)
        + f"\nNOT_BUILT_YET holds {sorted(NOT_BUILT_YET) or 'nothing'}, and everything else "
        f"is built: {', '.join(BUILT_TODAY)}."
    )


def test_a_verb_the_tables_do_call_unbuilt_may_be_described_as_unbuilt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Guards the case above against being a rule that simply bans a phrase.

    **THE UNBUILT NAME IS SUPPLIED RATHER THAN PICKED OFF THE TABLE, BECAUSE THE TABLE IS
    EMPTY**, which is the same move ``tests/test_cli_check.py`` makes and for the same reason:
    a version of this that drove whichever verb happened to be unbuilt would assert nothing at
    all today and would go on asserting nothing, silently, after somebody built the last one.

    What it holds is that the rule is about the tables and not about the words "not built". A
    document has to be able to say a settled-and-unbuilt verb is unbuilt -- that is what the
    row in ``NOT_BUILT_YET`` is for, and it is the sentence a reader most needs.
    """
    monkeypatch.setitem(NOT_BUILT_YET, "teleport", "put you on the machine without asking")
    document = tmp_path / "SOME.md"
    document.write_text("`edullm teleport` is not built yet.\n", encoding="utf-8")

    text = document.read_text(encoding="utf-8")
    claims = claims_about_what_is_built(text)

    assert claims, "the claim detector no longer recognises the plainest wording there is"
    assert not any(named & set(BUILT_TODAY) for _sentence, named in claims)


def test_the_wording_this_looks_for_is_the_wording_the_tree_actually_used() -> None:
    """Guards the detector itself, which is the half that can rot without going red.

    Mutation: tighten ``UNBUILT_CLAIM`` to a phrase nobody writes. Every document would carry
    no claims, every claim list would be empty, and the case above would pass over a file
    saying anything at all. That is the shape this repository has now found more than a dozen
    times, and a detector is the easiest place in a test file for it to hide.

    Both sentences below are verbatim: the first is what ``AGENTS.md`` said until 2026-08-05
    and the second is what ``docs-frank/working/what-you-can-test-tonight.md`` said. The second
    is the one that argues for :data:`NAMED_VERB` being wider than :data:`VERB_MENTION`, since
    it names its verbs with no ``edullm`` in front of any of them.
    """
    was_in_the_rule = "`edullm run` and `edullm shell` are settled and not built."
    was_in_the_local_note = (
        'Four more -- `run`, `shell`, `add`, `ask` -- print "not built yet" and a sentence '
        "about the plan."
    )

    for text, expected in (
        (was_in_the_rule, {"run", "shell"}),
        (was_in_the_local_note, {"run", "shell", "add", "ask"}),
    ):
        claims = claims_about_what_is_built(text)
        assert claims, f"the detector does not recognise: {text}"
        named = {verb for _sentence, verbs in claims for verb in verbs}
        assert expected <= named, f"{sorted(expected - named)} not read out of: {text}"
        assert expected <= set(BUILT_TODAY), "these are the verbs that were wrongly declared"


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


@pytest.mark.parametrize("document", AGENT_DOCUMENTS, ids=lambda path: path.name)
def test_the_document_writes_no_count_the_tree_owns(document: Path) -> None:
    """The half above cannot reach, and the same rule from the same module rather than a second.

    A configuration owns a threshold and the tree owns a count, so `edullm check --json` is
    the wrong place to send a reader for how many verbs there are. What it is right about is
    the shape of the mistake: the document is a copy, the copy is what goes stale, and
    nothing compares them. The shipped skill tables every verb the binary has and is correct
    today, which is exactly the state every claim in the 2026-08-06 sweep was in when it was
    written -- a sentence counting those rows would be the next one.
    """
    text = " ".join(document.read_text(encoding="utf-8").split())
    written = [
        (f"{document.name}:{line}", said, countable)
        for line, said, countable in count_claims([(1, text)])
    ]

    assert not written, point_it_at_the_tree(written, where=document.name)


def test_the_shipped_skill_names_every_exit_code_the_binary_can_return() -> None:
    """Mutation: document 0, 1 and 2 and leave 3 and 130 out.

    An agent branches on the exit code before it reads anything, and 3 is the only one of the
    five worth retrying. A skill that documented 2 and not 3 would produce exactly the script
    main.py's own header says was impossible before 3 existed: retry a typo forever, or never
    retry anything.
    """
    text = RESEARCHER_SKILL.read_text(encoding="utf-8")

    for code in (EXIT_OK, EXIT_REFUSED, EXIT_UNUSABLE, EXIT_UNREACHABLE, EXIT_INTERRUPTED):
        assert re.search(rf"(?<!\d){code}(?!\d)", text), (
            f"the skill documents no exit {code}, and every path out of the binary is one "
            "of the five"
        )


def test_the_shipped_skill_names_every_built_verb() -> None:
    """Mutation: describe check and submit and leave the read-only ones out.

    The skill's whole job is that an agent knows the binary exists and when to reach for it.
    A verb it does not name is a verb an agent writes a shell script for instead, which is
    the sixteen-against-nineteen problem the overview's agent layer describes.

    **THIS MOVED OFF AGENTS.md AND THE MOVE IS THE CORRECTION.** It was asserted against the
    always-on rule of a repository no researcher has a copy of, so the one document that
    reached the readers it was written for was the one nothing held. `stop`, `studio` and
    `console` were in AGENTS.md and in no shipped file, which is how somebody's `edullm run`
    machine goes unterminated: the verb that ends it was named only where they could not see.
    """
    text = RESEARCHER_SKILL.read_text(encoding="utf-8")
    missing = sorted(verb for verb in BUILT_TODAY if f"edullm {verb}" not in text)

    assert not missing, f"the skill names no {', '.join(missing)}"


def test_the_shipped_skill_says_the_binary_holds_no_aws_credential() -> None:
    """Mutation: drop the sentence, on the grounds that it is not actionable.

    It is the most actionable sentence in the file. An agent that does not know this reaches
    for boto3 or the aws CLI the moment edullm refuses something, and for the sixteen who
    hold no AWS role that produces a confusing failure, while for the nineteen who do it
    produces an unrecorded run. Covering that is the whole point of the layer.

    Read against the shipped skill and not against ``AGENTS.md``, which describes a checkout
    where holding an AWS session is the ordinary case rather than the thing being explained.
    """
    text = RESEARCHER_SKILL.read_text(encoding="utf-8").lower()

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
    """Every code a refusal can arrive under, from all three halves of the vocabulary.

    Read rather than listed, which is the point. One is the exception classes, which
    ``tests/test_refusal_codes.py`` already derives from the raise sites, and another is the
    ``Refusal(code=...)`` literals in the CLI package, which are the checks a laptop makes
    that the compile step has no exception for. A skill matches on a code without caring
    which half it came from, so a rule about a skill must not care either.

    **THE THIRD IS A HOLE THIS FILE HAD, AND ``unprovisioned_compute_profile`` IS THE ONE THAT
    FOUND IT.** ``raise_sites`` walks the package for anything raising a subclass of
    ``SubmissionRefusedError``, and ``ComputeProfileResolutionError`` in
    ``contracts/workload.py`` is a ``ValueError`` that carries a ``reason_code`` and is not
    one. ``cli/preflight.py`` catches it and builds ``Refusal(code=type(exc).reason_code)``,
    so the code reaches a caller's ``refusals`` exactly as the others do, and
    ``edullm check --compute gpu-8xh100`` prints it today. A skill tabulating it was being
    told nothing raises it, which is the shape of wrongness that gets a correct row deleted.

    So the class attribute is read wherever it is carried, by walking the same modules
    ``raise_sites`` walks and taking every ``reason_code`` assigned a string at class scope.
    That is wider than the raise sites and deliberately so, since what a caller can see is
    every code ``preflight`` can put on a ``Refusal`` rather than only the ones whose class
    happens to sit under one base.
    """
    from tests.test_refusal_codes import REASON_CODE, package_modules, raise_sites

    found = {site.code for site in raise_sites() if site.code}
    for _module, tree in package_modules():
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for statement in node.body:
                targets: list[ast.expr] = []
                if isinstance(statement, ast.AnnAssign):
                    targets = [statement.target]
                elif isinstance(statement, ast.Assign):
                    targets = list(statement.targets)
                value = getattr(statement, "value", None)
                if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                    continue
                if any(
                    isinstance(target, ast.Name) and target.id == REASON_CODE
                    for target in targets
                ):
                    found.add(value.value)
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
    assert "unprovisioned_compute_profile" in codes, (
        "a reason_code carried on a class outside the SubmissionRefusedError hierarchy is "
        "not being read, so a skill naming one is told nothing raises it"
    )


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


def registration_section() -> str:
    """The part of the shipped skill that registers a repository, and none of the rest.

    Scoped rather than read whole, which is the correction that made the base-image case a
    check at all. Written as ``"repositories.yaml" in text`` it passed against a document
    whose procedure had been replaced with "use whatever base the project's own Dockerfile
    names", because a closing list elsewhere still said the words; the mutation it names was
    run and did not kill it. An agent follows the steps, so the steps are what has to carry
    the answer.
    """
    text = RESEARCHER_SKILL.read_text(encoding="utf-8")
    heading = "\n## When the platform does not carry this codebase\n"

    assert heading in text, "the shipped skill no longer tells anybody how to register"
    section = text.split(heading, 1)[1].split("\n## ", 1)[0]
    assert section.strip(), "the registration section is empty"
    return section


def test_the_registration_procedure_writes_the_three_files_the_verb_does_not() -> None:
    """Mutation: describe `edullm add repository` and stop there.

    THE SPLIT IS THE WHOLE DESIGN AND IT IS EASY TO MISS. tools/register_repository.py edits
    eight platform files, reads the repository being registered, and opens the configuration
    pull request. It does not write the research repository's .edullm/Dockerfile, its
    build-caller workflow, that repository's AWS_ECR_PUBLISHER_ROLE_ARN variable or a first
    .edullm/run.yaml, and system-overview.md's agent layer assigns those to the skill. A
    registration with the pull request merged and none of them done is a repository that reads
    as registered and can never build an image.

    NO REPOSITORY IS NAMED AS AN EXAMPLE OF THAT STATE, ON PURPOSE. This docstring used to
    name one, the repository acquired both files the next day, and the sentence stayed --
    which is the same defect one level up from the one the tool now checks for. The state is
    describable without an instance, and whether any repository is currently in it is a
    question tools/verify_registered_dockerfiles.py answers every morning.
    """
    section = registration_section()

    for artifact in (".edullm/Dockerfile", ".edullm/run.yaml"):
        assert artifact in section, f"the registration procedure never writes {artifact}"
    assert "workflow" in section
    # The fourth thing, added 2026-08-06. It is not a file, which is why it was missed by a
    # list of files and why a repository could satisfy every assertion above and still publish
    # nothing.
    assert "AWS_ECR_PUBLISHER_ROLE_ARN" in section


def test_the_registration_procedure_resolves_against_the_approved_base_images() -> None:
    """Mutation: tell the agent to write whatever base image the project happens to use.

    system-overview.md's agent layer states the one question a reviewer answers: the skill
    resolves the dependency set against the base images repositories.yaml approves, and
    either picks the closest or names the pin forcing a new one. A second base is a second
    thing to review, scan and re-pin, and the skill choosing one silently is how that
    happens without anybody deciding it.
    """
    section = registration_section()

    assert "repositories.yaml" in section, (
        "the step that resolves the base image does not send the reader to the registry of "
        "approved ones"
    )
    assert "base image" in section.lower()


# ---------------------------------------------------------------------------------------
# The researcher's skill, which is the only document here that leaves this repository.
#
# EVERY CASE BELOW IS A THING THE SHARED ONES ABOVE CANNOT SEE. They hold a document to the
# binary's vocabulary: verbs it has, flags it takes, codes it can raise, no bound written
# out. A skill can pass all of that and still send somebody to `uv tool install edullm`,
# quote an approval class the policy cannot return, or print a bfloat16 spelling the guard
# stopped recognising. Those are claims rather than vocabulary, and a claim has to be held
# against the thing that decides it.
# ---------------------------------------------------------------------------------------


def researcher_skill() -> str:
    return RESEARCHER_SKILL.read_text(encoding="utf-8")


def first_column_under(header: str, *, text: str) -> set[str]:
    """The backticked first cell of every row of one table, and of no other table.

    Stops at the first line that is not a row, which is the whole of the care needed here.
    A scanner that filtered rather than stopped would run out of the table it was pointed at
    and into every one below it, and would then report the refusal codes as document keys.
    """
    lines = text.splitlines()
    assert header in lines, f"no table under {header!r}"
    found: set[str] = set()
    for line in lines[lines.index(header) + 2 :]:
        if not line.startswith("|"):
            break
        match = re.match(r"^\|\s*`([a-z][a-z0-9_]*)`\s*\|", line)
        if match is not None:
            found.add(match.group(1))
    return found


def test_the_researcher_skill_prints_the_install_line_the_binary_itself_prints() -> None:
    """Mutation: pin a tag into it, or write ``uv tool install edullm-platform``.

    ``cli/release.py``'s header records that the wrong install command sat in
    ``pyproject.toml`` for as long as it existed, and this skill is read by an agent that has
    no binary yet and therefore cannot ask it. So the line has to be the one the tool would
    have printed, read out of ``install_command`` rather than typed here, and unpinned: a tag
    written into a document is a version the document has to be edited for.
    """
    from edullm_platform.cli.actions import PLATFORM_REPOSITORY
    from edullm_platform.cli.release import install_command

    expected = install_command(repository=PLATFORM_REPOSITORY)
    printed = [line.strip() for line in fenced_lines(researcher_skill())]

    assert expected in printed, (
        f"no line the skill prints is exactly {expected!r}. A line with a tag on the end "
        "is not it: that is a version this file has to be edited for, and the line above "
        "is true after every release"
    )


def test_the_researcher_skill_names_the_distribution_rather_than_the_executable() -> None:
    """Mutation: leave the near miss out, on the grounds that the right line is above it.

    ``uv tool install edullm`` is the command somebody reaches for and uv answers
    ``not found in the package registry``. ``uv tool upgrade`` is the other one, and what it
    answers depends on how the tool was installed: it upgrades an install made from the bare
    URL and answers ``Nothing to upgrade`` to one pinned at a release tag, however far behind
    that one is. An agent that hits the first concludes the tool does not exist; one that
    hits the second on a pinned install concludes it is current, which costs a researcher a
    day of running against a configuration that has moved.

    **WHAT IS ASSERTED BELOW IS THAT THE SKILL MENTIONS THEM, AND NOTHING MORE.** Neither
    claim about uv is tested here. Both are tested by installing a package and running the
    commands, in ``tests/test_cli_install_command.py``. This one keeps the skill from going
    quiet about them.
    """
    from edullm_platform.cli.release import DISTRIBUTION

    text = researcher_skill()

    assert DISTRIBUTION in text, (
        f"the skill never names {DISTRIBUTION}, so nothing tells a reader why "
        "`uv tool install edullm` finds nothing"
    )
    assert "uv tool upgrade" in text, (
        "the skill says nothing about the upgrade command, so an agent that reaches for it "
        "cannot tell whether the answer it got means anything"
    )


def test_every_approval_class_the_researcher_skill_tabulates_is_one_the_policy_returns() -> None:
    """Mutation: keep a class the policy stopped returning, or invent a fourth.

    THE SKILL'S WHOLE ANSWER TO "WHO RELEASES THIS" IS THIS FIELD, WHICH IS THE POINT.
    ``config/policy.yaml``'s v5 note is a record of the classes being re-cut underneath
    everybody, and a skill that computed the answer from a threshold instead would have been
    silently wrong from that merge onwards. Reading ``approval_class`` is right and it only
    stays right while the values in the table are the values the enum has.
    """
    from edullm_platform.contracts.policy import ApprovalClass

    known = {member.value for member in ApprovalClass}
    tabulated = first_column_under(
        "| `approval_class` | Who releases the run |", text=researcher_skill()
    )

    assert tabulated, "the table under that heading has no rows"
    assert tabulated == known, (
        f"the skill tabulates {sorted(tabulated)} and ApprovalClass has {sorted(known)}"
    )


def test_the_researcher_skill_names_every_placement_verdict_capacity_can_carry() -> None:
    """Mutation: promote a fourth verdict and leave the skill reading the file for three.

    ``edullm check`` says nothing at all about whether EC2 will supply a shape, and a job it
    cannot supply sits in ``RUNNABLE`` with no error against it, which is indistinguishable
    from being queued. So the skill sends a reader at ``config/capacity.yaml`` in the
    install's own configuration directory, and a verdict it does not explain is one somebody
    reads off that file and cannot act on.
    """
    from edullm_platform.placement import PLACES_AFTER_A_WAIT, PLACES_RELIABLY, PLACES_UNRELIABLY

    text = researcher_skill()
    known = {PLACES_RELIABLY, PLACES_AFTER_A_WAIT, PLACES_UNRELIABLY}
    # Out of the table rather than out of the prose. A verdict named in a sentence and
    # missing from the table is one a reader meets with no reading against it, which is the
    # half that decides whether they pick the shape.
    tabulated = first_column_under("| What `places` says | What it means |", text=text)

    assert tabulated == known, (
        f"the skill tabulates {sorted(tabulated)} and capacity.yaml can carry {sorted(known)}"
    )
    assert "capacity.yaml" in text, "the skill sends nobody at the file that holds the verdicts"


def test_the_bfloat16_spelling_the_researcher_skill_prints_is_one_the_guard_reads() -> None:
    """**THE MOST EXPENSIVE THING THIS SKILL TEACHES, AND THE ONE MOST LIKELY TO ROT.**

    ``precision.py`` reads the text of a command and cannot see a dtype the program sets in
    code, and its own header names OLMo-core's entry point as the miss. So the skill tells an
    agent to write the dtype into the command, which turns a job that dies on a Turing card
    after being priced, released and placed into a refusal that costs nothing.

    That instruction is worth exactly as much as the spelling beside it. Held by running the
    detector over the line the skill prints rather than by searching for a word, so a
    narrowed :data:`~edullm_platform.precision.BFLOAT16_SPELLINGS` or a changed detector
    fails here instead of leaving a worked example that quietly stopped working.
    """
    import shlex

    from edullm_platform.errors import Bfloat16NotInTheHardwareError
    from edullm_platform.precision import bfloat16_request_in

    text = researcher_skill()
    assert Bfloat16NotInTheHardwareError.reason_code in text, (
        "the skill never names the refusal this whole section is about"
    )

    read = [
        found
        for line in fenced_lines(text)
        if "bfloat16" in line and not line.lstrip().startswith("#")
        and (found := bfloat16_request_in(shlex.split(line)))
    ]

    assert read, (
        "no command the skill prints is one bfloat16_request_in reads as a bfloat16 "
        "request, so the worked example teaches a way of naming the dtype that the guard "
        "no longer recognises and the refusal it promises would never fire"
    )


def test_every_key_the_researcher_skill_tabulates_is_a_key_a_check_emits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: rename a field in ``cli/machine.py`` and leave the skill reading the old one.

    The skill tells an agent to branch on ``refused``, read ``refusals``, price off ``cost``
    and route off ``approval_class``, and every one of those is a key rather than a word. A
    renamed key reaches the agent as a ``KeyError`` in the middle of somebody's request, and
    what it does next is read the paragraphs, which is the one thing the document exists to
    stop.

    Driven off a real invocation rather than off the source of ``check_document``, so the
    keys compared are the keys a caller sees.
    """
    import json

    from edullm_platform.cli.main import EXIT_OK
    from tests.cli_support import FakeRunner, git_answers, invoke, write_spec

    write_spec(tmp_path, compute="gpu-1xa10g")
    code, out, err = invoke(
        ["check", "--json", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment"],
        runner=FakeRunner(git_answers(tmp_path)),
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )
    assert code == EXIT_OK, out + err
    document = json.loads(out)

    tabulated = first_column_under("| Key | What it holds |", text=researcher_skill())

    assert tabulated, "the table under that heading has no rows"
    assert tabulated <= set(document), (
        f"the skill tabulates keys a check does not emit: {sorted(tabulated - set(document))}"
    )

    factors = [
        line for line in fenced_lines(researcher_skill()) if "maximum_compute_cost_usd =" in line
    ]
    assert factors, (
        "the skill no longer writes the product a fan-out multiplies, so nothing tells an "
        "agent that quoting one cell understates what somebody is approving"
    )
    named = set(re.findall(r"[a-z][a-z_]+", factors[0]))
    assert named == set(document["cost"]), (
        f"the cost arithmetic names {sorted(named)} and the document carries "
        f"{sorted(document['cost'])}"
    )


def test_the_page_that_installs_the_skill_points_at_a_file_that_is_here() -> None:
    """Mutation: rename the skill's folder and leave the copy line naming the old one.

    Nobody in the organization has this checkout, so the raw URL in that page is the whole
    of how the file reaches anybody. A dead one is a researcher who runs three commands, gets
    an empty file or an HTML error page, and concludes the skill does not exist.

    The branch in the URL is not checked, because nothing here can know what ``main`` holds.
    What is checked is the path under it, which is the half a rename breaks.
    """
    text = SKILLS_README.read_text(encoding="utf-8")
    paths = set(re.findall(r"raw\.githubusercontent\.com/[^/]+/[^/]+/[^/]+/(\S+)", text))

    assert paths, "the page carries no copy line at all"
    missing = sorted(path for path in paths if not (PROJECT_ROOT / path).is_file())
    assert not missing, f"the page points at files that are not in this tree: {missing}"

    expected = RESEARCHER_SKILL.relative_to(PROJECT_ROOT).as_posix()
    assert expected in paths, f"the page never sends anybody at {expected}"
