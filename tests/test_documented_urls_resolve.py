"""Every URL this repository writes at itself, held against the tree it points into.

**THE INSTALL LINE IS A URL AND A URL IS NOT A REFERENCE.** Nobody in the organization has
this checkout, so a raw URL is the whole of how a file reaches anybody: ``skills/README.md``
tells roughly the whole roster to ``curl`` one, and ``tools/set-up-a-laptop.sh`` is fetched by
its own. A rename inside this repository moves the file and leaves the URL pointing at
nothing, the reader gets ``curl: (56)`` or a GitHub 404 page, and nothing anywhere went red --
because the only thing that knew the two were connected was whoever wrote the sentence.

**THAT IS NOT HYPOTHETICAL AND IT IS WHY THIS IS REPOSITORY-WIDE.** On 2026-08-06 a branch
deleted ``skills/edullm-platform/SKILL.md``, which is the path in the install line the roster
had been given, and every gate on it was green: the file it deleted was deleted along with the
page naming it, so nothing was left disagreeing. ``tests/test_agent_layer.py`` already asked
this question of one page, which is the shape that catches the second mistake and never the
first -- a page can be correct about a file that a *different* page, script or workflow is
also pointing at.

**WHAT IS CHECKED IS THE PATH AND NOT THE NETWORK.** The branch component cannot be answered
from a working tree: nothing here knows what ``main`` holds, and a test that fetched would
fail on an aeroplane and pass on a pull request that is about to break the URL, since the
fetch would read the ``main`` the change has not landed on yet. The path under the branch is
the half a rename breaks and the half a checkout can answer. The other half is answered by the
``documented-urls`` job in ``.github/workflows/agent-layer-is-distributed.yml``, which fetches
every one of these against the real ``main``, daily. The pair is deliberate and it is the same
pair the distributor has: the tree goes red in the pull request that breaks it, the network
goes red within the day, and neither waits on the other.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: This repository, as the two hosts write it. Only its own URLs are held: a link at
#: ``edu-llm/OLMo-core`` is a claim about a repository this tree cannot see, and asserting on
#: one from here would go red for a reason nobody working on this change could act on.
OWNER_AND_REPOSITORY = "edu-llm/platform"

#: ``raw.githubusercontent.com/<owner>/<repo>/<ref>/<path>``, which is what a ``curl`` line
#: carries, and ``github.com/<owner>/<repo>/blob/<ref>/<path>``, which is what prose links to.
#: Both resolve to a path in this tree and a rename breaks both identically.
DOCUMENTED_URL = re.compile(
    rf"https://(?:raw\.githubusercontent\.com/{OWNER_AND_REPOSITORY}"
    rf"|github\.com/{OWNER_AND_REPOSITORY}/blob)"
    r"/(?P<ref>[^/\s]+)/(?P<path>[^\s\)\]\"'`]+)"
)

#: What a markdown sentence, a shell continuation or a YAML scalar leaves on the end of a URL
#: that is not part of the path.
TRAILING_PUNCTUATION = ".,;:!?'\"`)]}>\\"

#: A placeholder, which means the line is code that *builds* a URL rather than a URL somebody
#: was given.
#:
#: **THIS EXCLUSION WAS WRITTEN AFTER THE SCAN REPORTED ITS OWN WORKFLOW.** The
#: ``documented-urls`` job composes each URL from a ref and a path with an f-string, and the
#: template read as a URL whose path was ``{path``. That is a false report on correct code, and
#: a guard that cries wolf gets routed around rather than fixed -- one in this repository was,
#: three hours after it fired. No path in any tree here contains a brace, so nothing real is
#: lost, and a URL that has to be built is not an instruction anybody follows by hand.
TEMPLATED = re.compile(r"[{}]")

#: Files whose bytes are not text, and are not worth decoding to find out.
UNREADABLE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".pdf", ".ico", ".zip"})

#: THE PATHS ROUGHLY THE WHOLE ROSTER HAS IN A SHELL HISTORY, DECLARED HERE RATHER THAN
#: INFERRED FROM WHAT THE TREE HAPPENS TO SAY.
#:
#: **THE SCAN ABOVE CANNOT PROTECT THESE AND IT IS IMPORTANT TO SEE WHY.** It compares the
#: URLs a tree writes with the files a tree has, so a change that removes both sides at once
#: is consistent and passes. That is not a contrived mutation: it is exactly what
#: edu-llm/platform#398 does. It deletes ``skills/edullm-platform/SKILL.md`` and rewrites the
#: page whose ``curl`` line named it, in one commit, and every gate on that branch is green --
#: while everybody who was sent the line gets ``curl: (56)`` the next time they run it.
#:
#: So the fact is declared. Deleting one of these now requires editing this tuple, which is a
#: visible line in a diff with the cost written beside it, rather than a deletion that reads
#: as tidying up. **Editing it is allowed.** What is not allowed is doing it without
#: re-broadcasting: nothing in any repository can reach a URL somebody already has, so the
#: only thing that reaches those people is a message from a person, and the pull request that
#: moves one of these is where somebody has to decide to send it.
#:
#: A path here does not mean the file is frozen. Its *text* is edited freely and by anybody --
#: the URL names ``main``, so a merged edit reaches the next person to run the line with no
#: coordination at all. This is only about the path.
BROADCAST: tuple[str, ...] = ("skills/edullm-platform/SKILL.md",)


def tracked_files() -> list[str]:
    """Every file git knows about or would carry, which is the set CI sees.

    ``git ls-files`` rather than a walk, for the reason ``tests/test_cli_install_command.py``
    gives about the same choice: a walk reads a local virtualenv, a coverage database and
    whatever else the machine happens to be carrying, so it finds matches in CI that no
    laptop has and matches on a laptop that CI never sees.

    **``--others --exclude-standard`` IS HERE BECAUSE ITS ABSENCE COST A RED PULL REQUEST ON
    THE FIRST RUN OF THIS FILE.** With ``--cached`` alone a file that has been written and not
    yet staged is invisible, so the whole suite passed on a tree where the new workflow -- the
    one carrying the very URL this scan is about -- had not been added. It went red in CI the
    moment it was committed, which is the correct answer arriving at the least useful time.
    Everything is tracked in CI, so the flag changes nothing there and moves the finding-out
    to before the commit, which is the only place it is worth anything. Ignored files stay
    out: that is what ``--exclude-standard`` keeps, and it is what keeps a virtualenv out.
    """
    listed = subprocess.run(
        ("git", "ls-files", "--cached", "--others", "--exclude-standard"),
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return sorted({line for line in listed.stdout.splitlines() if line})


def documented_urls() -> list[tuple[str, int, str, str]]:
    """Every URL into this repository, as ``(file, line, ref, path)``."""
    found: list[tuple[str, int, str, str]] = []
    for name in tracked_files():
        path = PROJECT_ROOT / name
        if path.suffix.lower() in UNREADABLE_SUFFIXES or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for match in DOCUMENTED_URL.finditer(line):
                if TEMPLATED.search(match.group(0)):
                    continue
                target = match.group("path").rstrip(TRAILING_PUNCTUATION)
                if target:
                    found.append((name, number, match.group("ref"), target))
    return found


def test_every_url_this_repository_writes_at_itself_names_a_file_that_is_here() -> None:
    """**Mutation: rename or delete a file some page, script or workflow links to.**

    THE FAILURE THIS CATCHES IS SILENT ON BOTH SIDES. The person doing the rename is looking
    at the file, not at the sentence in another directory that points at it, and the person
    reading the sentence has no way to know it was ever true. What they get is an error from
    ``curl`` or a 404 page, and what they conclude is that the thing does not exist.

    The failure names the file and the line, because the fix is almost never to put the file
    back: it is to move the URL with it, and that needs the reader sent to the sentence.
    """
    missing = [
        f"{name}:{number}: /{ref}/{target}"
        for name, number, ref, target in documented_urls()
        if not (PROJECT_ROOT / target).exists()
    ]

    assert not missing, (
        "a URL into this repository names a path this tree does not have:\n  "
        + "\n  ".join(sorted(missing))
        + "\nThe file was moved, renamed or deleted and the URL was left behind. Point the "
        "URL at where the file went, or take the sentence out; a reader following it gets "
        "an error page and concludes the thing does not exist."
    )


@pytest.mark.parametrize("target", BROADCAST)
def test_the_file_the_roster_was_told_to_fetch_is_still_in_this_tree(target: str) -> None:
    """**Mutation: delete the file and the page's ``curl`` line in the same commit.**

    THAT IS THE ONE THE SCAN ABOVE LETS THROUGH, BECAUSE AFTER IT NOTHING DISAGREES WITH
    ANYTHING. Both halves of the contradiction are gone and the tree is internally consistent.
    The people it breaks are not in the tree: they are the ones holding a line they were sent,
    and no test can see them. Declaring the path is how they get a vote.
    """
    assert (PROJECT_ROOT / target).is_file(), (
        f"{target} has been moved or deleted, and it is a path this organization has already "
        "been told to fetch. Roughly the whole roster has that URL in a shell history and "
        "nothing in this repository can reach any of them.\n"
        "If that is intended: move it, point every URL at where it went, take the path out of "
        "BROADCAST in this file, and say in the pull request who is sending the message. If it "
        "is not intended, it is a rename that got ahead of the documents."
    )


@pytest.mark.parametrize("target", BROADCAST)
def test_something_in_this_tree_still_tells_somebody_how_to_fetch_it(target: str) -> None:
    """The other direction, and the one that keeps the pair honest.

    Mutation: keep the file and quietly stop documenting it. Everybody who already ran the
    line goes on fetching a file that is still served and no longer maintained as the thing
    anybody installs, and nobody arriving next week is told it exists at all. That is the
    silent-staleness case: it costs nothing today and it is undetectable later, because a file
    nothing points at looks exactly like a file nothing needs.
    """
    naming = {path for _name, _number, _ref, path in documented_urls()}

    assert target in naming, (
        f"{target} is in BROADCAST and no URL in this tree fetches it, so it is served, "
        "unmentioned, and still on the machines of everybody who ran the old line. Either "
        "document it or retire it deliberately: taking it out of BROADCAST is the second, and "
        "it is the line that says somebody has to tell those people."
    )


def test_the_scan_finds_the_urls_the_roster_is_actually_given() -> None:
    """Guards the case above, which passes over an empty list without saying so.

    Mutation: tighten :data:`DOCUMENTED_URL` until it matches nothing, or point
    :func:`tracked_files` at a directory. Every path would be in a set of none, the
    comparison would be against an empty left side, and the one rule holding the install
    line to the tree would go quiet without going red. This repository has now found that
    shape more than a dozen times and a detector is the easiest place for it to hide.

    The two below are named rather than counted because they are the two that reach a person
    who does not have this checkout: one is the line ``skills/README.md`` tells everybody to
    run, and the other is how ``set-up-a-laptop.sh`` reaches a laptop that has nothing.
    """
    found = {target for _name, _number, _ref, target in documented_urls()}

    assert set(BROADCAST) <= found, (
        f"the scan does not see {sorted(set(BROADCAST) - found)}, which is the install line "
        "itself. Either the page has stopped carrying it or this scan cannot read the page, "
        "and the second one is worse because everything else here would still be green"
    )
    assert "tools/set-up-a-laptop.sh" in found, (
        "the setup script's own fetch line is not being read, so the scan is not looking at "
        "shell scripts"
    )


def test_a_url_at_a_path_that_is_gone_is_what_this_reports() -> None:
    """Guards the reporting half, which is the half a reader acts on.

    Mutation: have the case above compare against the URL's own text rather than against the
    tree. It would pass on every well-formed URL, including one pointing at a file deleted
    that morning.
    """
    line = f"https://raw.githubusercontent.com/{OWNER_AND_REPOSITORY}/main/skills/gone.md"
    match = DOCUMENTED_URL.search(line)

    assert match is not None, "the plainest raw URL there is no longer matches"
    assert match.group("path") == "skills/gone.md"
    assert not (PROJECT_ROOT / match.group("path")).exists()


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        # A markdown link, which is how prose carries one and is the case the closing
        # parenthesis would otherwise be read into the path.
        (
            f"see [the page](https://github.com/{OWNER_AND_REPOSITORY}/blob/main/skills/README.md).",
            "skills/README.md",
        ),
        # A shell continuation, which is how every install line in this repository is
        # wrapped, and the backslash is not part of the file name.
        (
            f"  https://raw.githubusercontent.com/{OWNER_AND_REPOSITORY}/main/AGENTS.md \\",
            "AGENTS.md",
        ),
        # Backticked in prose.
        (
            f"`https://raw.githubusercontent.com/{OWNER_AND_REPOSITORY}/main/README.md`",
            "README.md",
        ),
    ],
)
def test_the_path_is_read_out_of_the_shapes_this_tree_writes_urls_in(
    written: str, expected: str
) -> None:
    """Mutation: read to the end of the line, or stop at the first punctuation there is.

    Either one reports a path that is not the path, and both do it on URLs that are correct.
    A guard that goes red on a working install line gets deleted rather than fixed, which is
    a worse outcome than not having written it: the next real break has nothing looking for
    it and a reason on the record for why not.
    """
    match = DOCUMENTED_URL.search(written)

    assert match is not None, f"no URL read out of: {written}"
    assert match.group("path").rstrip(TRAILING_PUNCTUATION) == expected
    assert not TEMPLATED.search(match.group(0)), "a real URL was mistaken for a template"


def test_a_line_that_builds_a_url_is_not_read_as_one() -> None:
    """Guards :data:`TEMPLATED`, which is an exclusion and therefore the dangerous kind of rule.

    Mutation: widen it until it swallows real URLs -- ``re.compile(".")`` is the extreme, and
    anything matching a character every URL has is the realistic one. Every URL in the tree
    would be skipped, the scan would compare an empty set, and this file would report green over
    an install line pointing at nothing.

    The line below is verbatim from ``.github/workflows/agent-layer-is-distributed.yml``, which
    is the code that produced the false report in the first place. The pair of assertions is the
    whole rule: the template is skipped, and the URL it renders to is not.
    """
    template = f"https://raw.githubusercontent.com/{OWNER_AND_REPOSITORY}/{{ref}}/{{path}}"
    rendered = f"https://raw.githubusercontent.com/{OWNER_AND_REPOSITORY}/main/AGENTS.md"

    templated = DOCUMENTED_URL.search(template)
    assert templated is not None, "the template no longer looks like a URL at all"
    assert TEMPLATED.search(templated.group(0)), "the placeholder is no longer recognised"

    real = DOCUMENTED_URL.search(rendered)
    assert real is not None
    assert not TEMPLATED.search(real.group(0)), (
        "the exclusion has been widened until it hides real URLs, which makes every case in "
        "this file pass over a tree with none left"
    )
    assert (PROJECT_ROOT / real.group("path")).is_file()
