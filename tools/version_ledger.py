"""Print every open pull request, what version it declares, and what it must declare.

**WHY THIS IS A COMMAND AND NOT A CHECK, WHICH IS THE WHOLE DESIGN.** The obvious repair for
two pull requests declaring the same version is a status check that refuses the collision,
and it is the wrong shape. A check is a claim a branch makes about itself, and this is not a
property of a branch: a pull request opened at noon is correct until a second one opens at
one, whereupon the first goes red through no act of its author, and the only remedy available
to them is to make somebody else red instead. Nothing about the second pull request is
knowable from inside the first.

The invariant is also not "no two share a version". It is "the versions form a chain in merge
order", which is a property of a *queue* -- of a decision nobody has made yet at the moment
any individual check runs. No branch has the authority to assert it, so nothing on the branch
path is asked to.

So this runs on a laptop, once, before somebody drains the queue. It reads and writes
nothing. It is allowed to know about all of them at once, because the person running it is
about to merge all of them at once, and that is exactly the authority a branch does not have.

**IT PRINTS INSTRUCTIONS RATHER THAN FACTS.** The reader is about to click twenty times in
sequence at the end of a long evening. A list of true statements about versions is a thing
they then have to do arithmetic on; the arithmetic is the part that has been got wrong three
times, so this does it and prints the answer.

**THE BUMP THAT JUSTIFIES ITSELF, WHICH IS THE CASE THAT COSTS A SLOT FOR NOTHING.**
``pyproject.toml`` is on the release-path list, so a change that touches nothing else and
bumps the version has, by touching it, made itself into a change that earns a release.
Circular, and it consumes a number out of a chain everything behind it has to step around.
This separates the two by reading the ``pyproject.toml`` diff: a hunk that is nothing but the
version line, the pinned install line and the reason comment is the bump itself and not a
reason for one, and the pull request is told to drop it.

**AND IT NAMES THE SQUASH HAZARD, BECAUSE THAT ONE IS INVISIBLE UNTIL IT HAS HAPPENED.**
Squashing a pull request that has another stacked on it puts a *new* commit on ``main``
holding the same tree. The parent's own commits are then not ancestors of ``main``, the child's
merge base falls back to before the stack, and every file the two have in common comes back as
a conflict -- measured on this repository's own queue at three files for one child and five for
another, on merges that are otherwise empty. So every pull request something is stacked on is
marked, and the mark says which button.

This holds no AWS credential and needs none. It shells out to ``gh``, which is the only
GitHub session on the machine, in the same way ``tools/report_asks.py`` does.

**IT NEEDS NO VERSION OF ITS OWN.** ``tools/`` is not on ``release-tag.yml``'s trigger list --
this file asks ``tools/release_paths.py`` rather than asserting it, and prints the answer at
the end of its own report, so the claim is re-checked every time anybody runs it.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIRECTORY = Path(__file__).resolve().parent
if str(TOOLS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIRECTORY))

from next_version import (
    REASON_PATTERN,
    SIZES,
    VERSION_PATTERN,
    VersionUnreadableError,
    next_version,
)
from release_paths import RELEASE_WORKFLOW, release_paths

__all__ = [
    "DEFAULT_REPOSITORY",
    "build_parser",
    "claimed_size",
    "declares",
    "earns_a_release",
    "ledger",
    "merge_order",
    "only_its_own_bump",
]

#: Where the queue is. An argument rather than a constant everywhere else, but a default is
#: what makes this a command somebody runs rather than one they look up first.
DEFAULT_REPOSITORY = "edu-llm/platform"

#: The file whose presence in a diff proves nothing on its own. See the module docstring.
VERSION_FILE = "pyproject.toml"


def _gh(*arguments: str) -> Any:
    """Ask ``gh`` for JSON, or raise with whatever it said on stderr.

    Failing loudly rather than returning an empty list, because every empty answer this could
    invent reads downstream as "the queue is clear", which is the one wrong answer that looks
    like good news.
    """
    completed = subprocess.run(
        ["gh", *arguments], capture_output=True, text=True, timeout=120, check=False
    )
    if completed.returncode != 0:
        raise RuntimeError(f"gh {' '.join(arguments)} failed: {completed.stderr.strip()}")
    return json.loads(completed.stdout)


def earns_a_release(filenames: list[str], paths: list[str]) -> list[str]:
    """The changed files that are on the release-path list, as the reader would name them."""
    return sorted(
        name
        for name in filenames
        if any(name == path or name.startswith(f"{path}/") for path in paths)
    )


def only_its_own_bump(patch: str | None) -> bool:
    """Whether a ``pyproject.toml`` hunk is the version bump and nothing else.

    Every changed line has to be one of the three ``tools/next_version.py`` writes: the
    version, the pinned install line it keeps in step, and the reason comment above them.
    Anything else -- a dependency, a classifier, a script entry -- is a real change to a
    release path and the pull request has earned its number.
    """
    if patch is None:
        return False
    changed = [line[1:].strip() for line in patch.splitlines() if line[:1] in "+-"]
    return bool(changed) and all(
        VERSION_PATTERN.fullmatch(line) is not None
        or REASON_PATTERN.fullmatch(line) is not None
        or "@v" in line
        for line in changed
    )


def declares(patch: str | None) -> tuple[str | None, str | None]:
    """The version a ``pyproject.toml`` hunk moves from, and the one it moves to."""
    if patch is None:
        return None, None
    was = then = None
    for line in patch.splitlines():
        found = VERSION_PATTERN.fullmatch(line[1:].strip()) if line[:1] in "+-" else None
        if found is None:
            continue
        if line[0] == "-":
            was = found["version"]
        else:
            then = found["version"]
    return was, then


def claimed_size(patch: str | None, was: str | None, then: str | None) -> str:
    """The size the author says this is, and only failing that the size they typed.

    **THE SENTENCE IS THE AUTHORITY AND THE ARITHMETIC IS THE FALLBACK**, which is the
    opposite of the obvious ordering and the reason this reads a comment at all. Subtracting
    the declared version from the one it replaced answers only for a branch cut off the
    current tip; every branch in a queue this size was cut off something staler, so the
    subtraction on a real pull request is ``4.5.0`` against ``4.11.0``, which is not one step
    of any size and would quietly come back a patch. That is the wrong answer in the one
    direction that costs something: a minor demoted to a patch publishes a release note with
    no Summary on a change that earned one.

    ``tools/next_version.py`` already writes the size into ``pyproject.toml`` as a comment
    above the version, so that a reviewer meets it in the diff. It is the author's own claim,
    it survives any amount of rebasing, and it is what ``release-tag.yml`` publishes. So it is
    what this believes.
    """
    for line in (patch or "").splitlines():
        if line[:1] == "+":
            found = REASON_PATTERN.fullmatch(line[1:].strip())
            if found is not None:
                return found["size"].lower()
    if was is not None and then is not None:
        for size in SIZES:
            try:
                if next_version(was, size) == then:
                    return size
            except VersionUnreadableError:
                # Only "not this size": the ceiling refusing major is a fact about the
                # answer rather than about the input.
                continue
    return "patch"


def merge_order(pulls: dict[int, dict[str, Any]], requested: list[int] | None) -> list[int]:
    """The order to merge in: the one asked for, or the stacks in dependency order.

    Derived from the base refs rather than from the numbers, because a stack is a statement
    about order that somebody already made and this should not second-guess it. Pull requests
    off ``main`` sort by number behind the stacks they may need rebasing onto.
    """
    if requested is not None:
        return requested
    heads = {pull["headRefName"]: number for number, pull in pulls.items()}
    ordered: list[int] = []

    def place(number: int) -> None:
        if number in ordered:
            return
        parent = heads.get(pulls[number]["baseRefName"])
        if parent is not None:
            place(parent)
        ordered.append(number)

    for number in sorted(pulls):
        place(number)
    return ordered


def ledger(
    pulls: dict[int, dict[str, Any]], order: list[int], released: str, paths: list[str]
) -> list[str]:
    """The report, as the lines to print. Pure, so ``tests`` can read it without a network."""
    heads = {pull["headRefName"]: number for number, pull in pulls.items()}
    stacked_on = {heads[p["baseRefName"]] for p in pulls.values() if p["baseRefName"] in heads}
    running = released
    # THE BRANCH THE NEXT BUMP HAS TO SIT ON TOP OF, WHICH IS THE ADVICE THAT MAKES THE REST
    # ACTIONABLE. Two pull requests that both move `pyproject.toml` and share a base conflict
    # on it by construction, however far apart their other files are, so the rebase target
    # worth naming is not `main` but whichever one took the previous number.
    beneath = "main"
    lines = [
        f"Latest release v{released}. {len(order)} open pull requests, in merge order.",
        "",
        f"{'PR':>5}  {'DECLARES':<9} {'MUST BE':<9} WHAT TO DO",
    ]
    actions: list[str] = []
    for number in order:
        pull = pulls[number]
        touched = earns_a_release([f["filename"] for f in pull["files"]], paths)
        patch = next(
            (f.get("patch") for f in pull["files"] if f["filename"] == VERSION_FILE), None
        )
        was, then = declares(patch)
        gratuitous = only_its_own_bump(patch)
        earned = [name for name in touched if name != VERSION_FILE or not gratuitous]
        if not earned:
            must, what = "-", "no release path. Needs no version"
            if then is not None:
                what = f"DROP the bump to {then}. Its only release path is its own bump"
                actions.append(f"#{number}: revert {VERSION_FILE} and uv.lock, then re-push.")
        else:
            size = claimed_size(patch, was, then)
            must = next_version(running, size)
            what = "ok" if then == must else f"RE-BUMP from {then or 'nothing'} to {must}"
            if then != must:
                why = ' --why "..."' if size != "patch" else ""
                onto = "" if pull["baseRefName"] == beneath else f"rebase onto {beneath}, then "
                actions.append(
                    f"#{number}: {onto}"
                    f"uv run python tools/next_version.py --bump {size}{why}   # {must}"
                )
            running, beneath = must, pull["headRefName"]
        draft = " (draft)" if pull["isDraft"] else ""
        lines.append(f"{'#' + str(number):>5}  {then or '-':<9} {must:<9} {what}{draft}")
    if stacked_on:
        lines += [
            "",
            "MERGE COMMIT, NOT SQUASH, for "
            + ", ".join(f"#{n}" for n in sorted(stacked_on))
            + ".",
            "Something is stacked on each. Squashing puts a new commit on main, leaves the",
            "original commits off it, and every file the two share comes back as a conflict.",
        ]
    if actions:
        lines += ["", "DO THIS, in order:", *(f"  {line}" for line in actions)]
    mine = "tools/version_ledger.py"
    verdict = "is" if earns_a_release([mine], paths) else "is not"
    lines += ["", f"{mine} {verdict} on the release-path list, so it needs no version."]
    return lines


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Print every open pull request, what version it declares, and what it "
        "must declare to leave an unbroken chain behind a merge order. Reads only."
    )
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument("--workflow", type=Path, default=RELEASE_WORKFLOW)
    parser.add_argument(
        "--order",
        default=None,
        help="the merge order to check, as comma-separated numbers; defaults to the stacks "
        "in dependency order, which is the order the base refs already assert",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        paths = release_paths(arguments.workflow.read_text(encoding="utf-8"))
        released = _gh("release", "view", "--repo", arguments.repository, "--json", "tagName")[
            "tagName"
        ].lstrip("v")
        pulls = {}
        for summary in _gh(
            "pr", "list", "--repo", arguments.repository, "--state", "open", "--limit", "200",
            "--json", "number,baseRefName,headRefName,isDraft",
        ):
            summary["files"] = _gh(
                "api", "--paginate", f"repos/{arguments.repository}/pulls/{summary['number']}/files"
            )
            pulls[summary["number"]] = summary
        requested = (
            [int(part) for part in arguments.order.split(",")] if arguments.order else None
        )
        print("\n".join(ledger(pulls, merge_order(pulls, requested), released, paths)))
    except (RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
