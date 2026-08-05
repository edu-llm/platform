"""Print the paths a change has to touch to earn a release, as git pathspecs.

**ONE LIST, READ TWICE, RATHER THAN TWO LISTS THAT AGREE UNTIL THEY DO NOT.**
``release-tag.yml`` triggers on a set of paths and ``ci.yml`` has to ask the same question
before the merge, because the version a change is released as has to be declared by the pull
request making the change. GitHub Actions gives a workflow no way to share a list with
another workflow, so the second copy is the obvious thing to write and the wrong thing: the
direction that hurts is a path added to the trigger and not to the guard, after which a
configuration change merges having never been asked to declare a version, and the merge that
should have cut the release goes red instead.

So there is one list. It lives in ``release-tag.yml``, where the trigger has to be a literal
because Actions filters the event before any job starts, and this reads it out of that file
for anybody who needs it in a shell.

**AND IT TRANSLATES, WHICH IS THE OTHER HALF.** An Actions path filter and a git pathspec are
different languages that look identical for most inputs, and ``src/edullm_platform/cli/**``
is not one of the inputs where they agree well enough to trust. Actions defines ``**`` as
"any number of characters including the separator"; git's default pathspec matching would
also match it, by a different rule that a future `:(glob)` magic prefix or a change of
matcher would quietly break. A directory prefix is the thing git is unambiguous about, so
that is what comes out. Every other pattern is emitted as itself, and anything carrying a
wildcard this cannot translate is refused rather than guessed at, because a pathspec that
matches nothing reports every change as touching nothing and passes everything.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

__all__ = ["build_parser", "pathspec_for", "release_paths", "trigger_paths"]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RELEASE_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "release-tag.yml"

#: The suffix on a directory pattern, which is the only wildcard form this accepts.
DIRECTORY_SUFFIX = "/**"


class ReleasePathsUnreadableError(RuntimeError):
    """The trigger list is not where this expects it, or holds a pattern it cannot translate."""


def trigger_paths(workflow_text: str) -> list[str]:
    """The ``on.push.paths`` list, in the order the workflow writes it.

    ``yaml.safe_load`` reads the unquoted key ``on`` as the boolean ``True``, which is the
    one place a workflow file and YAML disagree about what a document says. Both spellings
    are accepted rather than one being assumed, because which one appears depends on how the
    file was written and neither is wrong.
    """
    document = yaml.safe_load(workflow_text)
    if not isinstance(document, dict):
        raise ReleasePathsUnreadableError("the release workflow is not a YAML mapping")
    triggers = document.get("on", document.get(True))
    push = triggers.get("push") if isinstance(triggers, dict) else None
    paths = push.get("paths") if isinstance(push, dict) else None
    if not isinstance(paths, list) or not paths:
        raise ReleasePathsUnreadableError(
            "the release workflow declares no on.push.paths, so nothing here can tell which "
            "changes earn a release"
        )
    return [str(path) for path in paths]


def pathspec_for(pattern: str) -> str:
    """One Actions path filter as the git pathspec that selects the same files."""
    if pattern.endswith(DIRECTORY_SUFFIX):
        directory = pattern[: -len(DIRECTORY_SUFFIX)]
        if "*" in directory:
            raise ReleasePathsUnreadableError(
                f"{pattern!r} globs above its directory, and this translates only a whole "
                "directory or a whole file"
            )
        return directory
    if "*" in pattern or "?" in pattern or "[" in pattern:
        raise ReleasePathsUnreadableError(
            f"{pattern!r} is a wildcard this cannot translate into a git pathspec. Write the "
            f"files out, or write the directory as <directory>{DIRECTORY_SUFFIX}."
        )
    return pattern


def release_paths(workflow_text: str) -> list[str]:
    return [pathspec_for(pattern) for pattern in trigger_paths(workflow_text)]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Print, one per line, the git pathspecs a change must touch to earn a "
        "release."
    )
    parser.add_argument(
        "--workflow",
        type=Path,
        default=RELEASE_WORKFLOW,
        help="the workflow whose push trigger owns the list",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        for pathspec in release_paths(arguments.workflow.read_text(encoding="utf-8")):
            print(pathspec)
    except (OSError, ReleasePathsUnreadableError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
