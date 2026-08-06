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
from tests.test_cli_no_hardcoded_bounds import (
    WRITTEN_BOUND,
    count_claims,
    point_it_at_the_tree,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENTS = PROJECT_ROOT / "AGENTS.md"

#: WHY THIS IS ``.agents/`` AND NOT ``.cursor/``, WHICH IS THE DEFECT THAT MOVED IT. Under
#: ``.cursor/`` these two loaded for an agent working on this platform, in one host, and for
#: nobody working in OLMo-core or a codebase of their own -- which is precisely who the layer
#: was asked for. Codex reads project skills out of ``.agents/skills`` and out of nowhere
#: else, Cursor reads it natively, and ``.claude/skills/<name>`` is a symlink onto the same
#: file because Claude Code reads nothing but its own directory. One text, three hosts.
SKILLS = PROJECT_ROOT / ".agents" / "skills"

REGISTERING = SKILLS / "registering-a-repository" / "SKILL.md"

#: THE ALWAYS-ON RULE AS IT IS SHIPPED, WHICH IS NOT THIS REPOSITORY'S OWN ``AGENTS.md``.
#: ``AGENTS.md`` above is guidance for somebody working on the platform. This is the block
#: spliced into every registered repository's ``AGENTS.md`` by
#: ``tools/distribute_agent_layer.py``, and it is the only part of the layer a researcher's
#: agent loads without being asked to. The install line lives here rather than in a skill
#: because installing is not a situation an agent recognises, it is the thing that has to
#: have already happened.
RESEARCHER_RULE = PROJECT_ROOT / "skills" / "agents-md-block.md"
SKILLS_README = PROJECT_ROOT / "skills" / "README.md"

#: Every document in this layer. The shared properties below are parametrized over it, so a
#: skill added later cannot arrive unheld.
#:
#: **TWO DOCUMENTS WERE REMOVED ON 2026-08-06 AND THE REASON IS THE SAME ONE TWICE.**
#: ``skills/edullm-platform/SKILL.md`` was the two skills merged plus install instructions, at
#: more lines than both together, and was never put to the owner as an addition to the set.
#: ``submitting-a-run`` went for the sharper reason: its refusal table restated in prose what
#: ``check`` now prints in the ``detail`` beside every code, which is the founding rule of this
#: layer inverted. What in it was not already in the rule was folded into the rule first.
AGENT_DOCUMENTS: tuple[Path, ...] = (
    AGENTS,
    REGISTERING,
    RESEARCHER_RULE,
    SKILLS_README,
)

#: The skills in this layer, which is one. Held apart from the rules because the frontmatter
#: and the length budget are properties of a skill and not of a rule. A tuple rather than a
#: single path so that a second skill, if one is ever approved, arrives held.
SKILL_DOCUMENTS: tuple[Path, ...] = (REGISTERING,)

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
#: The header of a table of refusal codes, which no document in this layer may carry any more.
#: See :data:`RESTATED_TABLES`, where the argument is. Kept as a constant rather than inlined
#: because it is the one banned table that reads like an obviously helpful thing to add back.
CODE_TABLE_HEADER = "| Code | What to do |"


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
    nothing compares them. AGENTS.md counts the verbs today and is correct today, which is
    exactly the state every claim in the 2026-08-06 sweep was in when it was written.
    """
    text = " ".join(document.read_text(encoding="utf-8").split())
    written = [
        (f"{document.name}:{line}", said, countable)
        for line, said, countable in count_claims([(1, text)])
    ]

    assert not written, point_it_at_the_tree(written, where=document.name)


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


def test_the_registration_skill_names_the_code_that_is_its_trigger() -> None:
    """Mutation: rename the refusal and leave the skill describing the old one.

    THIS IS THE ONE CODE THE LAYER STILL CANNOT DO WITHOUT NAMING, WHICH IS WHY IT HAS A CASE
    OF ITS OWN NOW THAT THE TABLES ARE GONE. The rule tells an agent to switch to this skill
    when ``check`` refuses with this code, and the skill's own description says the same, so
    the code is the join between the two documents. Renamed in the package and not here, the
    skill goes on existing and nothing ever reaches it.

    Read off the exception rather than typed, for the reason ``errors.py``'s header gives
    about the same string: a second spelling of a rule is a second answer to a settled
    question, and this file would otherwise be the third place the code is written down.
    """
    from edullm_platform.errors import UnregisteredRepositoryError

    code = UnregisteredRepositoryError.reason_code
    frontmatter = skill_frontmatter(REGISTERING)

    assert code in frontmatter["description"], (
        f"the skill's description never names {code}, so a host matching a refusal against "
        "it has nothing to match on"
    )
    assert code in RESEARCHER_RULE.read_text(encoding="utf-8"), (
        f"the always-on rule never names {code}, so nothing sends an agent from a refused "
        "check to the one skill that answers it"
    )


def test_the_registration_skill_writes_the_three_files_the_verb_does_not() -> None:
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
    text = REGISTERING.read_text(encoding="utf-8")

    for artifact in (".edullm/Dockerfile", ".edullm/run.yaml"):
        assert artifact in text, f"the registration skill never writes {artifact}"
    assert "workflow" in text
    # The fourth thing, added 2026-08-06. It is not a file, which is why it was missed by a
    # list of files and why a repository could satisfy every assertion above and still publish
    # nothing.
    assert "AWS_ECR_PUBLISHER_ROLE_ARN" in text


def test_the_registration_skill_resolves_against_the_approved_base_images() -> None:
    """Mutation: tell the agent to write whatever base image the project happens to use.

    system-overview.md's agent layer states the one question a reviewer answers: the skill
    resolves the dependency set against the base images repositories.yaml approves, and
    either picks the closest or names the pin forcing a new one. A second base is a second
    thing to review, scan and re-pin, and the skill choosing one silently is how that
    happens without anybody deciding it.

    **READ OF THE PROCEDURE AND NOT OF THE WHOLE FILE, WHICH IS THE CORRECTION THAT MADE THIS
    A CHECK AT ALL.** Written as `"repositories.yaml" in text` it passed against a skill whose
    step 2 had been replaced with "use whatever base the project's own Dockerfile names",
    because the closing Never list still said the words. The mutation it names was run and did
    not kill it. An agent follows the steps, so the steps are what has to carry the answer.
    """
    text = REGISTERING.read_text(encoding="utf-8")
    procedure = text.split("\n## Never", 1)[0]

    assert procedure != text, "the skill has no Never section, so this is reading the lot"
    assert "repositories.yaml" in procedure, (
        "the step that resolves the base image does not send the reader to the registry of "
        "approved ones, so a Never list saying it does is the only thing left saying it"
    )
    assert "base image" in procedure.lower()


# ---------------------------------------------------------------------------------------
# The always-on rule, which is the document that leaves this repository.
#
# EVERY CASE BELOW IS A THING THE SHARED ONES ABOVE CANNOT SEE. They hold a document to the
# binary's vocabulary: verbs it has, flags it takes, codes it can raise, no bound written
# out. A document can pass all of that and still send somebody to `uv tool install edullm`,
# or print a bfloat16 spelling the guard stopped recognising. Those are claims rather than
# vocabulary, and a claim has to be held against the thing that decides it.
#
# THE TABLES THESE CASES USED TO HOLD ARE GONE ALONG WITH THE DOCUMENTS THAT CARRIED THEM,
# AND THE LAST CASE IN THIS FILE IS WHY. `submitting-a-run` tabulated the approval classes,
# the placement verdicts, the check document's keys and the cost arithmetic, and each table
# had a case here comparing it against the tree. Comparing them was the second-best answer.
# The best one is not writing them down: `check` prints all four, in the document the rule
# already tells the agent to read, so the tables were a copy of an artifact sitting next to
# the artifact. What replaced four comparison cases is one rule saying no document may carry
# them, which is smaller and cannot go stale.
# ---------------------------------------------------------------------------------------


def researcher_rule() -> str:
    return RESEARCHER_RULE.read_text(encoding="utf-8")


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


def test_the_always_on_rule_prints_the_install_line_the_binary_itself_prints() -> None:
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
    printed = [line.strip() for line in fenced_lines(researcher_rule())]

    assert expected in printed, (
        f"no line the skill prints is exactly {expected!r}. A line with a tag on the end "
        "is not it: that is a version this file has to be edited for, and the line above "
        "is true after every release"
    )


def test_the_always_on_rule_names_the_distribution_rather_than_the_executable() -> None:
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

    text = researcher_rule()

    assert DISTRIBUTION in text, (
        f"the skill never names {DISTRIBUTION}, so nothing tells a reader why "
        "`uv tool install edullm` finds nothing"
    )
    assert "uv tool upgrade" in text, (
        "the skill says nothing about the upgrade command, so an agent that reaches for it "
        "cannot tell whether the answer it got means anything"
    )


def test_the_bfloat16_spelling_the_rule_prints_is_one_the_guard_reads() -> None:
    """**THE MOST EXPENSIVE THING THIS LAYER TEACHES, AND THE ONE MOST LIKELY TO ROT.**

    ``precision.py`` reads the text of a command and cannot see a dtype the program sets in
    code, and its own header names OLMo-core's entry point as the miss. So the rule tells an
    agent to write the dtype into the command, which turns a job that dies on a Turing card
    after being priced, released and placed into a refusal that costs nothing. It is one of
    the lines folded out of ``submitting-a-run`` before that skill was deleted, and it is the
    only one of them carrying a worked example.

    That instruction is worth exactly as much as the spelling beside it. Held by running the
    detector over the line the rule prints rather than by searching for a word, so a narrowed
    :data:`~edullm_platform.precision.BFLOAT16_SPELLINGS` or a changed detector fails here
    instead of leaving a worked example that quietly stopped working.
    """
    import shlex

    from edullm_platform.errors import Bfloat16NotInTheHardwareError
    from edullm_platform.precision import bfloat16_request_in

    text = researcher_rule()
    assert Bfloat16NotInTheHardwareError.reason_code in text, (
        "the rule never names the refusal this whole paragraph is about"
    )

    # Every command the rule shows, whether fenced or in backticks mid-sentence. Both are
    # read because the dtype example is one clause of a bullet rather than a block of its
    # own, and a test that only understood fences would pass on a rule that had stopped
    # printing a command at all.
    candidates = [*fenced_lines(text), *re.findall(r"`([^`\n]+)`", text)]
    read = [
        found
        for candidate in candidates
        if "bfloat16" in candidate and (found := bfloat16_request_in(shlex.split(candidate)))
    ]

    assert read, (
        "no command the rule prints is one bfloat16_request_in reads as a bfloat16 request, "
        "so the worked example teaches a way of naming the dtype that the guard no longer "
        "recognises and the refusal it promises would never fire"
    )


#: The four things ``edullm check --json`` prints that a document used to tabulate instead.
#: Each is a table that existed, was compared against the tree by a case in this file, and was
#: deleted with the skill that carried it. The header is what is banned rather than the words,
#: because a document has to be able to *name* ``approval_class`` -- telling the agent to read
#: it is the entire remedy -- and must not be able to enumerate what it can hold.
RESTATED_TABLES = (
    CODE_TABLE_HEADER,
    "| `approval_class` | Who releases the run |",
    "| What `places` says | What it means |",
    "| Key | What it holds |",
    "maximum_compute_cost_usd =",
)


@pytest.mark.parametrize("document", AGENT_DOCUMENTS, ids=lambda path: path.name)
def test_no_document_tabulates_what_a_check_already_prints(document: Path) -> None:
    """**The founding rule of this layer, enforced forwards instead of by comparison.**

    A skill reads the generated artifact rather than restating policy in prose. Every table
    banned here was a restatement of something ``check`` prints in the same document the rule
    already sends the agent to, and each one was individually held against the tree by a case
    in this file, and one of them was wrong anyway: ``submitting-a-run``'s predecessor said an
    ``exception`` run is released by a platform admin, and ``classify_request`` has not
    returned that class since policy v5. The case guarding it compared the table against
    ``ApprovalClass``, which still carries the member for reserved-capacity purchases, so a
    test bound to the vocabulary sat green over a false claim about the routing.

    THE LESSON IS THAT COMPARING A COPY IS A WEAKER MOVE THAN NOT KEEPING ONE. A comparison
    can be bound to the wrong artifact and nothing says so. This rule cannot: there is no
    table, so there is nothing to be wrong. It is also the cheaper rule to keep true, which is
    why it is here rather than four cases reinstated against a different document.
    """
    text = document.read_text(encoding="utf-8")
    written = [table for table in RESTATED_TABLES if table in text]

    assert not written, (
        f"{document.name} tabulates what edullm check --json already prints: "
        f"{written}. Tell the agent to read the field out of the document instead. "
        "A table here is a copy of an artifact sitting next to the artifact, and the last "
        "one drifted into telling researchers to find an admin for a run no admin sees."
    )


def test_the_page_names_the_directory_each_host_actually_reads() -> None:
    """Mutation: leave the old ``curl`` lines in, or drop a host from the table.

    THAT PAGE TOLD PEOPLE TO PUT THE SKILL IN ``~/.codex/skills`` AND CODEX DOES NOT READ
    THAT PATH. Anybody who followed it got a file no host loaded and no error saying so,
    which is the failure mode every part of this layer has: an agent with no skill behaves
    exactly like an agent whose skill did not match, so nobody finds out.

    The three paths are asserted because the three hosts disagree and the disagreement is
    load bearing. ``.agents/skills`` is Codex's only project directory, ``.claude/skills``
    is Claude Code's only one, and Cursor reads both. Dropping any row from that table is
    how one third of the organization silently stops having the layer.
    """
    text = SKILLS_README.read_text(encoding="utf-8")

    for path in (".agents/skills", ".claude/skills"):
        assert path in text, f"the page never names {path}"
    for host in ("Cursor", "Claude Code", "Codex"):
        assert host in text, f"the page says nothing about {host}"
    assert "CLAUDE.md" in text and "AGENTS.md" in text, (
        "the page does not distinguish the two root files, and they are not "
        "interchangeable: Claude Code reads one of them and the other two hosts read the "
        "other"
    )
    assert "distribute_agent_layer" in text, (
        "the page does not send anybody at the distributor, so the way to get the layer "
        "into a repository is once again copying it by hand"
    )


def test_the_page_installs_the_skill_where_codex_will_go_on_reading_it() -> None:
    """Guards the case above against being satisfied by a page that says both things.

    **THE FACT THIS CASE ASSERTS WAS CORRECTED ONCE ALREADY AND THAT IS THE POINT OF IT.**
    The page used to tell people to put the skill in ``~/.codex/skills``, which was first read
    here as a path Codex does not read at all -- wrong, and wrong in the direction that would
    have had somebody delete a working install. Codex does read it, from a path its own source
    comments mark deprecated and kept for backward compatibility, while ``~/.agents/skills`` is
    the documented one. So the page may mention the old path, and what it may not do is send
    anybody there to install: an instruction that works by luck expires without warning.

    Asserted on the copy commands rather than on the prose, because the prose has to be free
    to explain the deprecation -- that explanation is the reason the next person does not
    "fix" this back.
    """
    text = SKILLS_README.read_text(encoding="utf-8")
    installs = [
        line
        for line in fenced_lines(text)
        if any(verb in line for verb in ("cp ", "ln -s", "curl", "mkdir -p"))
    ]

    assert installs, "the page carries no install commands at all"
    assert any("~/.agents/skills" in line for line in installs), (
        "no install command puts the skill in ~/.agents/skills, which is Codex's documented "
        "user-level path and the only one it will keep reading"
    )
    assert not any("~/.codex/skills" in line for line in installs), (
        "an install command still puts the skill in ~/.codex/skills. Codex reads it today and "
        "has marked it deprecated, so this works by luck and will stop without warning"
    )
    assert "raw.githubusercontent.com" not in text, (
        "the page tells somebody to curl a copy for themselves. Copies made that way are the "
        "ones nothing holds equal and nothing knows about"
    )
