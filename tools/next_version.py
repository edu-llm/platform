"""Read ``project.version``, and write the next patch version back over it.

WHY A TOOL RATHER THAN A SED LINE IN THE WORKFLOW. The version this prints becomes a tag,
a GitHub release, and the string every installed CLI compares itself against, so a
malformed answer is not a broken build -- it is a release nobody's staleness check can
parse, which fails silent in the direction of "you are current". Here it can be tested,
and ``tests/test_next_version.py`` is where the cases live.

**AND IT MOVES ``uv.lock`` IN THE SAME BREATH, WHICH IS NOT A TIDINESS.** The lock records
the root distribution's own version, ``uv sync --locked`` fails when that disagrees with
``pyproject.toml``, and that command is the first step of every CI job here. A bump that
wrote only ``pyproject.toml`` would be pushed straight to ``main`` by ``release-tag.yml``
with no pull request in front of it, and the next person to open one would find a red
build they did not cause and cannot fix from their branch. So the two files move together
or neither does.

The lock is edited by anchored substitution rather than by running ``uv lock``, for one
reason beyond keeping this stdlib-only: a resolver on a release runner would have to reach
every dependency including the git ones, and a release is not the moment to discover that
an unrelated upstream moved. ``uv lock`` writes exactly the line this writes -- verified by
running it -- and this cannot write anything else.

The patch component and only the patch component. A minor or major bump is a statement
about what changed and nobody should be able to make it by merging; the workflow that
calls this cuts a release per merge touching the CLI or the configuration, which is a
volume no human judgement can keep up with. Where somebody does want to say more than
"another one", they edit ``project.version`` in the pull request and the workflow tags what
they wrote rather than bumping past it.

Deliberately stdlib only, and deliberately not a TOML *writer*. ``tomllib`` reads and
cannot write; every writer reformats the file, and this file is nine tenths comment. So the
read is a parse and the write is an anchored substitution of the one line the parse agrees
with -- and it refuses rather than guessing if those two disagree.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

__all__ = [
    "VERSION_PATTERN",
    "lock_version_pattern",
    "next_patch_version",
    "read_lock_version",
    "read_name",
    "read_version",
    "rewrite_lock_version",
    "rewrite_pinned_tag",
    "rewrite_version",
]

#: ``version = "0.2.0"`` at the start of a line, which is the only place hatchling reads it
#: from and the only line this may touch. Anchored to the line start so a version inside a
#: dependency specifier or a comment cannot match.
VERSION_PATTERN = re.compile(r'^version\s*=\s*"(?P<version>[^"]+)"\s*$', re.MULTILINE)

#: Three dotted integers and nothing else. Narrower than PEP 440 on purpose: the tag is
#: ``v`` plus this, and a release tag with a local version or a pre-release segment in it
#: is a comparison the CLI's probe would have to be taught, for a release nobody here cuts.
SEMANTIC_VERSION = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$")


class VersionUnreadableError(RuntimeError):
    """``project.version`` is absent, malformed, or not where the substitution can reach."""


def read_version(text: str) -> str:
    """The declared version, parsed as TOML rather than matched out of the text."""
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise VersionUnreadableError(f"pyproject.toml is not valid TOML: {exc}") from exc
    project = document.get("project")
    version = project.get("version") if isinstance(project, dict) else None
    if not isinstance(version, str) or not version:
        raise VersionUnreadableError("pyproject.toml declares no project.version")
    return version


def read_name(text: str) -> str:
    """``project.name``, which is what the lock files the root package under.

    Read rather than written down, because a second copy of the distribution name is the
    exact mistake the CLI's install line spent two transcripts on.
    """
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise VersionUnreadableError(f"pyproject.toml is not valid TOML: {exc}") from exc
    project = document.get("project")
    name = project.get("name") if isinstance(project, dict) else None
    if not isinstance(name, str) or not name:
        raise VersionUnreadableError("pyproject.toml declares no project.name")
    return name


def lock_version_pattern(distribution: str) -> re.Pattern[str]:
    """The root package's own two lines in ``uv.lock``, and no other package's.

    ``uv`` writes ``name`` immediately above ``version`` in every ``[[package]]`` table, so
    matching the pair is what makes this specific: forty-odd packages in that file have a
    ``version`` line and only one of them is this project's.
    """
    return re.compile(
        rf'^name\s*=\s*"{re.escape(distribution)}"\r?\nversion\s*=\s*"(?P<version>[^"]+)"\s*$',
        re.MULTILINE,
    )


def read_lock_version(text: str, *, distribution: str) -> str:
    """What the lock currently believes the root package's version is."""
    found = lock_version_pattern(distribution).search(text)
    if found is None:
        raise VersionUnreadableError(
            f"uv.lock has no [[package]] entry for {distribution!r} with a version line "
            "under its name, so nothing here can keep it in step with pyproject.toml"
        )
    return found["version"]


def rewrite_lock_version(text: str, *, distribution: str, version: str) -> str:
    """Move the lock's record of the root version, or refuse to touch the file at all."""
    pattern = lock_version_pattern(distribution)
    found = pattern.findall(text)
    if len(found) != 1:
        raise VersionUnreadableError(
            f"expected exactly one uv.lock entry for {distribution!r} to rewrite, "
            f"found {len(found)}"
        )
    return pattern.sub(f'name = "{distribution}"\nversion = "{version}"', text, count=1)


def rewrite_pinned_tag(text: str, *, was: str, now: str) -> str:
    """Move every ``@v<version>`` pin in the file from one version to the next.

    ``pyproject.toml`` carries the install line pinned to the declared version, and
    ``tests/test_cli_install_command.py`` asserts the two agree -- deliberately, because the
    line being wrong and unread for the whole life of the project is what that file exists
    about. A bump that moved the declaration and left the pin would fail that test on
    ``main``, with no pull request in front of it and nobody's change to blame.

    Substituting the pin rather than regenerating the line, because regenerating it means
    knowing how it is spelled, and there is exactly one place that knows: this runs from a
    bare checkout with nothing installed, so it cannot ask.
    """
    return text.replace(f"@v{was}", f"@v{now}")


def next_patch_version(version: str) -> str:
    """``0.2.0`` to ``0.2.1``. Refuses anything it cannot increment unambiguously."""
    matched = SEMANTIC_VERSION.fullmatch(version)
    if matched is None:
        raise VersionUnreadableError(
            f"{version!r} is not major.minor.patch, so there is no next patch version. "
            "Releases here are tagged v<version> and compared as three integers."
        )
    return (
        f"{matched['major']}.{matched['minor']}.{int(matched['patch']) + 1}"
    )


def rewrite_version(text: str, version: str) -> str:
    """Replace the one ``version = "..."`` line, or refuse to touch the file at all.

    Refusing on more than one match matters more than it looks: this repository's
    ``pyproject.toml`` carries a second ``[project]``-shaped table in ``client/``, and a
    day when the two files are merged is a day a blind substitution rewrites the client's
    version to the platform's.
    """
    found = VERSION_PATTERN.findall(text)
    if len(found) != 1:
        raise VersionUnreadableError(
            f"expected exactly one top-level version line to rewrite, found {len(found)}"
        )
    return VERSION_PATTERN.sub(f'version = "{version}"', text, count=1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print project.version, or bump its patch component and print the new one."
    )
    parser.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    parser.add_argument(
        "--lock",
        type=Path,
        default=None,
        help="the uv.lock to keep in step; defaults to the one beside --pyproject",
    )
    parser.add_argument(
        "--bump",
        action="store_true",
        help="write the next patch version back to the file before printing it",
    )
    arguments = parser.parse_args(argv)
    lock_path = (
        arguments.lock if arguments.lock is not None else arguments.pyproject.parent / "uv.lock"
    )
    text = arguments.pyproject.read_text(encoding="utf-8")
    try:
        version = read_version(text)
        if arguments.bump:
            was, version = version, next_patch_version(version)
            # BOTH REWRITES ARE COMPUTED BEFORE EITHER IS WRITTEN. A half-applied bump is a
            # tree whose two files disagree, which is precisely the state that fails
            # `uv sync --locked` -- so the failure mode of guarding against it must not be
            # the thing it guards against.
            rewritten = rewrite_pinned_tag(rewrite_version(text, version), was=was, now=version)
            lock_text = lock_path.read_text(encoding="utf-8") if lock_path.exists() else None
            lock_rewritten = (
                rewrite_lock_version(
                    lock_text, distribution=read_name(text), version=version
                )
                if lock_text is not None
                else None
            )
            arguments.pyproject.write_text(rewritten, encoding="utf-8")
            if lock_rewritten is not None:
                lock_path.write_text(lock_rewritten, encoding="utf-8")
    except VersionUnreadableError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
