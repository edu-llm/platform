"""Read ``project.version``, and write the next patch version back over it.

WHY A TOOL RATHER THAN A SED LINE IN THE WORKFLOW. The version this prints becomes a tag,
a GitHub release, and the string every installed CLI compares itself against, so a
malformed answer is not a broken build -- it is a release nobody's staleness check can
parse, which fails silent in the direction of "you are current". Here it can be tested,
and ``tests/test_next_version.py`` is where the cases live.

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

__all__ = ["VERSION_PATTERN", "next_patch_version", "read_version", "rewrite_version"]

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
        "--bump",
        action="store_true",
        help="write the next patch version back to the file before printing it",
    )
    arguments = parser.parse_args(argv)
    text = arguments.pyproject.read_text(encoding="utf-8")
    try:
        version = read_version(text)
        if arguments.bump:
            version = next_patch_version(version)
            arguments.pyproject.write_text(
                rewrite_version(text, version), encoding="utf-8"
            )
    except VersionUnreadableError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
