"""The one command a newcomer types, held to one spelling by something that can fail.

THIS FILE EXISTS BECAUSE THE COMMAND WAS WRONG FOR AS LONG AS IT EXISTED AND NOBODY RAN IT.
``pyproject.toml`` said ``uv tool install edullm --from git+…``; uv answers "Package name
(``edullm-platform``) provided with ``--from`` does not match install request (``edullm``)"
and installs nothing. It was a comment, so no test read it, no lint saw it, and by the time
anybody typed it the line had been copied into two terminal transcripts as the first thing
each of them showed a researcher doing.

A comment cannot be trusted with the only install instruction a project has, so the
instruction now lives in :mod:`edullm_platform.cli.release` and every other copy is checked
against it here. The same rule catches the other half: ``uv tool upgrade`` reports
``Nothing to upgrade`` for a git-installed tool, so a guide that suggests it tells a
researcher they are current when they are not, and no file may say it without saying that.

THREE RULES ARE READ OVER THE TRACKED TREE, AND THE THIRD IS THE ONE THAT WAS MISSING. No
file may recommend the upgrade command, no file may name the wrong install line, and no
file may carry the right install line without saying that re-running it is the upgrade.
The third was the gap: a file that mentions ``uv tool upgrade`` nowhere passes the first
rule by saying nothing, which is exactly what ``README.md`` did while carrying the install
line on the front page. The corpus is ``git ls-files`` rather than a walk of the checkout,
because the walk read whatever a working directory happened to hold and failed on one
laptop over a gitignored document, which is a failure nobody in CI can see and therefore
nobody owns.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tomllib
from pathlib import Path

from edullm_platform.cli.actions import PLATFORM_REPOSITORY
from edullm_platform.cli.release import DISTRIBUTION, TAG_PATTERN, install_command

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
RELEASE_TAG_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "release-tag.yml"

#: Written as an escape rather than as itself, so this file can name the character it
#: forbids without carrying one.
EM_DASH = "\u2014"

#: What uv is being asked to do. Spelled once so the rule below can quote it rather than
#: three assertions each carrying their own copy.
UPGRADE_COMMAND = "uv tool upgrade"

#: The sentence uv actually answers a ``uv tool upgrade`` with here. A file allowed to
#: mention the command has to carry this too, which is the difference between warning about
#: it and recommending it.
UPGRADE_REFUTATION = "Nothing to upgrade"

#: The console script written where a distribution belongs. Both wrong install lines this
#: project has evidence of start here: ``uv tool install edullm --from git+...``, which sat
#: in ``pyproject.toml``'s comment unrun, and a bare ``uv tool install edullm``, which the
#: owner was handed by an assistant and which answers "not found in the package registry".
BROKEN_INSTALL = "uv tool install edullm"

#: uv's answers to the two wrong lines, either of which is enough. Same bargain as
#: ``UPGRADE_REFUTATION``: naming the broken command is allowed, because it has to be
#: written down somewhere or the next person reinvents it, but only beside the sentence
#: that says it does not work.
#:
#: **TWO PHRASES RATHER THAN ONE, BECAUSE THE TWO SPELLINGS FAIL DIFFERENTLY AND
#: ``BROKEN_INSTALL``'S OWN NOTE ALREADY SAID SO.** The first is what uv answers the
#: ``--from`` form. The second is what a bare ``uv tool install edullm`` answers, verified
#: on uv 0.9.17 against PyPI, which carries neither ``edullm`` nor ``edullm-platform``. A
#: rule accepting only the first made a document quoting uv's real answer to the command it
#: was warning about the thing this refuses, which teaches the next author to paste a
#: sentence uv never says.
BROKEN_REFUTATIONS = ("does not match install request", "not found in the package registry")

#: The suffixes a person reads instructions out of. Everything else this repository tracks
#: is data, generated, or binary.
READABLE_SUFFIXES = frozenset({".md", ".toml", ".yml", ".yaml", ".py", ".sh", ".txt"})


def declared_version() -> str:
    return str(tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"])


def readable_files() -> list[Path]:
    """Every tracked file a person reads instructions out of.

    ASKED OF GIT RATHER THAN OF THE FILESYSTEM, BECAUSE THE FILESYSTEM ANSWERS A DIFFERENT
    QUESTION ON EVERY MACHINE. This used to be an ``os.walk`` of the checkout with a prune
    list, which reads whatever a working directory happens to contain -- so the rules below
    matched a gitignored planning document under ``docs-frank/`` that exists on one laptop
    and nowhere else. Three agents in one evening hit that failure, each confirmed it also
    failed on ``main``, each correctly concluded it was not theirs, and none of them owned
    it, which is what a test that cannot fail in CI buys.

    Tracked, rather than a hand-written list of the four documents that carry the install
    line today. The bug being policed is a wrong line *spreading*: it reached two terminal
    transcripts before anybody ran it, and neither of those would have been on a list.
    ``git ls-files`` is the same set on every machine and in CI, it is exactly what a push
    carries, and a guide added tomorrow is inside it without anybody remembering to say so.
    The prune list went with the walk: nothing in ``.git``, ``.venv`` or a cache is tracked,
    and the generated trees that were pruned by name hold no readable suffix.

    A tracked path deleted in the working tree is skipped rather than read. It has no
    content to police, and the guard below is what keeps that from quietly emptying this.
    """
    listing = subprocess.run(
        ("git", "ls-files", "-z"),
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return sorted(
        path
        for name in listing.split("\0")
        if name and Path(name).suffix in READABLE_SUFFIXES
        if (path := PROJECT_ROOT / name).is_file()
    )


def flattened(path: Path) -> str:
    """The file as one line, so a rule below is not defeated by where a sentence wrapped.

    Every phrase these rules look for is long enough to wrap, and two of them already do:
    ``release.py``'s docstring breaks the wrong install line after ``install`` and breaks
    uv's answer after ``match``. A rule that a reformat can switch off is a rule nobody can
    rely on, so the comparison is made against text with its line breaks collapsed.
    """
    return " ".join(path.read_text(encoding="utf-8", errors="replace").split())


def test_the_command_in_pyproject_is_the_one_the_code_spells() -> None:
    """Mutation: edit the comment. The comment is where the broken one lived.

    Compared against the generated line rather than against a second literal here, because
    a literal here would be a third copy and the bug was a second copy.
    """
    expected = install_command(
        repository=PLATFORM_REPOSITORY, tag=f"v{declared_version()}"
    )

    assert expected in PYPROJECT.read_text(encoding="utf-8")


def test_the_install_command_is_an_invocation_uv_would_accept() -> None:
    """The specific failure that shipped: ``--from`` beside a mismatched install request.

    ``--from`` names a distribution to install *from*, and the word after it is then a
    different distribution found in that source. Naming the console script there is the
    error uv reports, and it is invisible to anybody who has not run it -- which is
    everybody who reads a comment.
    """
    words = shlex.split(install_command(repository=PLATFORM_REPOSITORY, tag="v9.9.9"))

    assert words[:3] == ["uv", "tool", "install"]
    assert "--from" not in words
    requirements = [word for word in words[3:] if not word.startswith("-")]
    assert requirements == [f"git+https://github.com/{PLATFORM_REPOSITORY}@v9.9.9"]


def test_the_install_command_is_also_the_upgrade() -> None:
    """Mutation: drop ``--force``.

    Without it, installing a newer tag over an existing install is an error rather than a
    replacement, so the researcher needs a second command -- and the second command they
    reach for is the one uv answers ``Nothing to upgrade`` to.
    """
    assert "--force" in shlex.split(install_command(repository=PLATFORM_REPOSITORY))


def test_the_pinned_and_unpinned_forms_differ_only_by_the_ref() -> None:
    unpinned = install_command(repository=PLATFORM_REPOSITORY)
    pinned = install_command(repository=PLATFORM_REPOSITORY, tag="v1.2.3")

    assert pinned == f"{unpinned}@v1.2.3"


def test_the_declared_version_is_a_version_a_tag_can_be_cut_from() -> None:
    """``release-tag.yml`` cuts ``v<version>``, and the CLI's probe parses what it cut.

    Mutation: write a pre-release or a local version into ``project.version``. The tag
    would still be cut, and the CLI would then discard its own release as unparseable --
    which fails silently in the direction of "you are current".
    """
    assert TAG_PATTERN.fullmatch(f"v{declared_version()}")


def test_the_release_note_this_project_publishes_carries_no_em_dash() -> None:
    """Mutation: put the character back into the heredoc that composes the note.

    That heredoc is delivered verbatim as the body of every release this workflow cuts,
    and a release note is read at one moment -- after ``edullm submit`` has told
    somebody their install is behind. So a house rule about published prose is a rule
    about this file before it is a rule about anything else, and the em dash is the one
    rule with a single character to look for. Asserted over the whole file rather than
    the heredoc alone: the comments here are quoted in review and the step names are
    read off the Actions tab, so there is no part of it worth exempting.
    """
    assert EM_DASH not in RELEASE_TAG_WORKFLOW.read_text(encoding="utf-8"), (
        f"{RELEASE_TAG_WORKFLOW.name} carries an em dash, so every release note cut "
        "from it carries one too. Write the sentence with a full stop instead."
    )


def test_the_version_has_moved_off_the_one_that_never_moved() -> None:
    """Mutation: revert to 0.1.0.

    It read 0.1.0 for the whole life of the project, so ``edullm --version`` answered the
    same thing for an install from today and one from six weeks earlier, and a staleness
    check against it checked nothing. The specific dead value is asserted rather than a
    floor, because the point is that the number is maintained by something now.
    """
    assert declared_version() != "0.1.0"


def test_the_distribution_is_not_the_console_script() -> None:
    """The confusion the broken command was made of, read out of the metadata itself."""
    document = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))

    assert document["project"]["name"] == DISTRIBUTION
    assert list(document["project"]["scripts"]) == ["edullm"]
    assert DISTRIBUTION != "edullm"


def test_what_a_researcher_is_told_to_type_is_what_the_code_spells() -> None:
    """Mutation: hand-write the install line into the guide, or let the URL drift.

    The three places addressed to somebody who has not installed this yet. Held to the
    *unpinned* line on purpose: it is the one that is true after every release, and pinning
    the documentation to a version would put the guide and the README into the set of files
    a bump has to rewrite, which is a coupling that has already gone wrong three times.
    Re-running the unpinned line is the upgrade, which is the property being documented.

    ``AGENTS.md`` is here for the same reason and one worse. It is loaded into every agent
    session on this repository, so a tag written into it is a stale number read more often
    than any other line in the tree, by a reader with no reason to doubt it and no habit of
    checking. It is also the file that tells an agent never to quote a number from a
    document, which it would then be doing.
    """
    unpinned = install_command(repository=PLATFORM_REPOSITORY)

    for path in (
        PROJECT_ROOT / "README.md",
        PROJECT_ROOT / "guides" / "the-platform.md",
        PROJECT_ROOT / "AGENTS.md",
    ):
        assert unpinned in path.read_text(encoding="utf-8"), (
            f"{path.name} does not carry the install line, so the only instruction a "
            "newcomer has is either absent or a second copy that can rot"
        )


def test_the_guide_says_what_check_costs_and_that_it_reaches_nothing() -> None:
    """Mutation: document ``check`` as a validator and leave out that it is free.

    Whether it is worth running half a dozen times while editing is the whole of why the
    verb is shaped this way, and a reader who assumes it calls GitHub runs it once. The
    same property is what makes it work on a cluster login node with no egress.
    """
    guide = (PROJECT_ROOT / "guides" / "the-platform.md").read_text(encoding="utf-8")

    section = guide.split("## From a terminal", 1)
    assert len(section) == 2, "the platform guide no longer tells anybody the CLI exists"
    body = section[1].split("\n## Keeping", 1)[0]

    assert "no egress" in body
    assert "edullm check" in body


def test_nothing_recommends_the_upgrade_command_that_does_not_work() -> None:
    """Mutation: write "then run uv tool upgrade" into a guide.

    Verified on uv 0.9.17 against a real install from this repository: ``uv tool upgrade
    edullm-platform`` answers ``Nothing to upgrade``, and so does the same command with
    ``--reinstall``. So the rule is not that the phrase is forbidden -- it has to be
    written down somewhere or nobody learns why -- but that a file mentioning it must also
    carry uv's actual answer. Recommending it and warning about it look identical to a
    grep. They do not look identical to this.
    """
    offenders = [
        str(path.relative_to(PROJECT_ROOT))
        for path in readable_files()
        if UPGRADE_COMMAND in (text := flattened(path)) and UPGRADE_REFUTATION not in text
    ]

    assert not offenders, (
        f"these name `{UPGRADE_COMMAND}` without saying that uv answers "
        f"{UPGRADE_REFUTATION!r} to it for a git-installed tool:\n  "
        + "\n  ".join(offenders)
        + f"\nSay so beside it, or print the {install_command(repository=PLATFORM_REPOSITORY)} "
        "line instead."
    )


def test_nothing_tells_anybody_to_install_the_console_script() -> None:
    """Mutation: write the old ``--from`` line, or a bare ``uv tool install edullm``, into
    a tracked document.

    The rule above catches a *replaced* install line, because it asserts the right one is
    present. It cannot catch an *added* wrong one, and added is how this shipped: the
    broken command lived in a comment beside nothing, was read by no test and no linter,
    and was copied into two transcripts as the first thing a researcher types. Tonight the
    owner typed the other spelling of the same confusion, a bare ``uv tool install
    edullm``, and got "not found in the package registry" -- so the failure is not one
    historical line but the console script standing where the distribution belongs.

    Both spellings begin the same way, which is what makes one cheap phrase enough.
    """
    offenders = [
        str(path.relative_to(PROJECT_ROOT))
        for path in readable_files()
        if BROKEN_INSTALL in (text := flattened(path))
        and not any(refutation in text for refutation in BROKEN_REFUTATIONS)
    ]

    assert not offenders, (
        f"these name `{BROKEN_INSTALL}` without saying that uv refuses it -- `edullm` is "
        f"the console script and {DISTRIBUTION} is the distribution:\n  "
        + "\n  ".join(offenders)
        + f"\nWrite {install_command(repository=PLATFORM_REPOSITORY)} instead, or quote "
        f"one of uv's answers ({' / '.join(BROKEN_REFUTATIONS)}) beside it."
    )


def test_every_file_that_says_how_to_install_says_how_to_upgrade() -> None:
    """Mutation: delete the upgrade sentence from ``README.md``.

    The two halves were held separately and only one of them was enforced everywhere. Any
    file may be checked for recommending ``uv tool upgrade``, but a file that never
    mentions it passes that check by saying nothing -- and saying nothing is the failure.
    Somebody who installs successfully reaches for ``upgrade`` next, and uv answers
    ``Nothing to upgrade`` with a zero exit whatever the state of the install, so the
    obvious command tells them they are current when they are months behind.

    ``README.md`` was in exactly that state when this rule was written: it carried the
    install line, in the one paragraph on the front page addressed to somebody about to
    type it, and said nothing about what to do next. So the pairing is the rule. A file
    that tells a researcher how to install has to tell them how to stay current, because it
    is the only file some of them will read.
    """
    unpinned = install_command(repository=PLATFORM_REPOSITORY)
    offenders = [
        str(path.relative_to(PROJECT_ROOT))
        for path in readable_files()
        if unpinned in (text := flattened(path)) and UPGRADE_REFUTATION not in text
    ]

    assert not offenders, (
        "these carry the install line without saying that re-running it is the upgrade, "
        f"so a reader who reaches for `{UPGRADE_COMMAND}` is told {UPGRADE_REFUTATION!r} "
        "and believes it:\n  "
        + "\n  ".join(offenders)
        + f"\nSay beside the line that {UPGRADE_COMMAND} answers {UPGRADE_REFUTATION!r} "
        "for a git-installed tool and that re-running the line is how you upgrade."
    )


def test_the_rules_above_read_the_repository_and_not_somebody_s_working_directory() -> None:
    """Guards the corpus at both ends, because it has already been wrong at both.

    Too narrow is the shape this repository keeps finding: a scan scoped until it matches
    nothing passes forever and reports success. So the files the three rules exist for are
    named here, and losing any of them is a failure rather than a quiet green.

    Too wide is the bug this scoping fixed, and it is asserted rather than described. An
    untracked file is written into the checkout and the corpus must not contain it, which
    is the whole difference between the old walk and ``git ls-files``. It carries no phrase
    any rule looks for, so the workers this suite runs beside cannot trip over it while it
    exists.
    """
    assert {"pyproject.toml", "README.md", "release-tag.yml", "main.py"} <= {
        path.name for path in readable_files()
    }

    probe = PROJECT_ROOT / f".untracked-probe-{os.getpid()}.md"
    probe.write_text("Written and removed by the test below it.\n", encoding="utf-8")
    try:
        assert probe not in readable_files(), (
            f"{probe.name} is untracked and was read anyway, so these rules answer "
            "differently depending on what is lying around in somebody's checkout"
        )
    finally:
        probe.unlink()
