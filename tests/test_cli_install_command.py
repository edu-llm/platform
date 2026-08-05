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
"""

from __future__ import annotations

import os
import shlex
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

#: The suffixes a person reads instructions out of. Everything else in this tree is data,
#: generated, or binary.
READABLE_SUFFIXES = frozenset({".md", ".toml", ".yml", ".yaml", ".py", ".sh", ".txt"})

#: Directories with nothing addressed to a researcher in them, pruned rather than filtered
#: so the walk does not descend into a virtualenv. ``docs-frank`` is not listed because it
#: is not in this repository at all.
PRUNED = frozenset(
    {".git", ".venv", ".mypy_cache", ".pytest_cache", "__pycache__", "schemas", "proof"}
)


def declared_version() -> str:
    return str(tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"])


def readable_files() -> list[Path]:
    found: list[Path] = []
    for directory, subdirectories, filenames in os.walk(PROJECT_ROOT):
        subdirectories[:] = [name for name in subdirectories if name not in PRUNED]
        found.extend(
            Path(directory) / filename
            for filename in filenames
            if Path(filename).suffix in READABLE_SUFFIXES
        )
    return sorted(found)


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
        if UPGRADE_COMMAND in (text := path.read_text(encoding="utf-8", errors="replace"))
        and UPGRADE_REFUTATION not in text
    ]

    assert not offenders, (
        f"these name `{UPGRADE_COMMAND}` without saying that uv answers "
        f"{UPGRADE_REFUTATION!r} to it for a git-installed tool:\n  "
        + "\n  ".join(offenders)
        + f"\nSay so beside it, or print the {install_command(repository=PLATFORM_REPOSITORY)} "
        "line instead."
    )


def test_the_rule_above_is_reading_files_that_exist() -> None:
    """Guards the walk: a bad prune list would make the case above vacuously pass."""
    names = {path.name for path in readable_files()}

    assert {"pyproject.toml", "README.md", "release-tag.yml", "main.py"} <= names
