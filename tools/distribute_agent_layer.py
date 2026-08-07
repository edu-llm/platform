"""Writes the always-on rule into a research checkout, and says when a copy has drifted.

**WHAT IS DISTRIBUTED IS THE RULE, AND THE ONE SKILL IS DELIBERATELY NOT.** ``AGENTS.md`` is
loaded every session without being invoked, it is most of what an agent needs, and it is what
makes anything else reachable -- so it has to be in the repository the agent has open. A
researcher working in OLMo-core gets nothing from a file that exists only in a platform
checkout, which is where it lived until now, and the observed consequence is an agent that
writes ``boto3`` against a cluster it cannot reach.

``registering-a-repository`` is the opposite case and putting it here would be a category
error. It fires when a codebase is *not* on the platform, and an unregistered codebase has no
``AGENTS.md`` either, so committing the skill to the six repositories that are already
registered installs it in exactly the six places it can never be needed. It belongs at user
level, and ``skills/README.md`` argues where and how.

**WHAT THEN HOLDS THE COPIES EQUAL.** Copies drift, and prose that has drifted is worse than
prose that is missing because nothing looks wrong. Two things answer that and neither is
discipline. This module is the only supported way to write a copy, so nobody edits one by
hand; and ``tests/test_agent_layer_is_distributed.py`` reads every registered repository's
copy off GitHub and fails when it stops matching the source here. The test is the load
bearing half. A distributor without it is a convention, and a convention is what drift is
made of.

**WHY THE RULE IS A BLOCK RATHER THAN A FILE.** ``AGENTS.md`` is read whole and a
repository's own half of it -- how to run its tests, what its layout means -- is the half
the platform has no business writing. OLMo-core's says its trainer sets bfloat16 in code
where the platform's guard cannot see it, which is knowledge no platform file has. So the
rule is written between two markers and everything outside them is left exactly as it was
found. The markers are what let this run twice.

The same asymmetry applies one level up: Cursor and Codex read ``AGENTS.md`` and Claude Code
reads ``CLAUDE.md`` and has no setting that changes that. Where a repository has no
``CLAUDE.md`` this writes a one-line one that imports ``AGENTS.md``, so that the text exists
once. Where it already has one, that file is left alone and is assumed to point at
``AGENTS.md`` itself, because a repository with a substantial ``CLAUDE.md`` has made a
choice about which of the two is primary and it is not this script's to reverse.
"""

from __future__ import annotations

import argparse
import difflib
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent

AGENTS_SOURCE = PROJECT_ROOT / "skills" / "agents-md-block.md"
REPOSITORIES_YAML = PROJECT_ROOT / "config" / "repositories.yaml"

BEGIN_MARKER = "<!-- edullm:begin -->"
END_MARKER = "<!-- edullm:end -->"

MARKER_NOTE = (
    "<!-- Managed by edu-llm/platform. Edit skills/agents-md-block.md there and re-run\n"
    "     tools/distribute_agent_layer.py; an edit made here is reverted and, until it is,\n"
    "     tests/test_agent_layer_is_distributed.py is red. Text outside the markers is\n"
    "     this repository's own and is never touched. -->"
)

#: Claude Code's documented way of pulling in another file, and the whole of the bridge.
#:
#: **A ``CLAUDE.md`` THAT MERELY EXISTS IS NOT ENOUGH, WHICH IS THE CORRECTION THAT PUT THIS
#: HERE.** Four of the six registered repositories already had one, written before any of
#: this and saying nothing about the platform. Writing the rule into ``AGENTS.md`` and
#: checking only that ``CLAUDE.md`` was present would have left Claude Code reading a file
#: that does not mention ``edullm``, in the repositories most likely to be worked in, while
#: every check reported the layer installed. The import is what makes the one text reach the
#: third host.
CLAUDE_IMPORT = "@AGENTS.md"

CLAUDE_BRIDGE = (
    f"{CLAUDE_IMPORT}\n"
    "\n"
    "<!-- Claude Code reads this file and does not read AGENTS.md, so this imports it.\n"
    "     Keep the guidance in AGENTS.md rather than here, so there is one text. -->\n"
)


@dataclass(frozen=True)
class Divergence:
    """One path whose content in a checkout is not the content this repository holds."""

    path: str
    detail: str


def registered_repositories() -> list[str]:
    """The repositories to distribute to, read off the reviewed configuration.

    Not a list in this file. A seventh repository is registered by merging a change to
    ``config/repositories.yaml``, and a hand-kept list here would mean that repository is
    registered, buildable, submittable and carrying no agent layer, with nothing red.
    """
    document = yaml.safe_load(REPOSITORIES_YAML.read_text())
    entries = document.get("repositories", document) if isinstance(document, dict) else document
    return sorted(entry["repository"] for entry in entries)


def expected_agents_block() -> str:
    """The managed region of a research repository's ``AGENTS.md``, markers included."""
    return f"{BEGIN_MARKER}\n{MARKER_NOTE}\n\n{AGENTS_SOURCE.read_text().strip()}\n{END_MARKER}"


def splice_block(existing: str, block: str) -> str:
    """``existing`` with the managed region replaced, or the block appended where there is none.

    Idempotent by construction: splicing an already-current file returns it unchanged, which
    is what lets this be run on every repository whenever the source moves.
    """
    start = existing.find(BEGIN_MARKER)
    end = existing.find(END_MARKER)
    if start != -1 and end != -1 and end > start:
        return existing[:start] + block + existing[end + len(END_MARKER) :]
    if not existing.strip():
        return f"# AGENTS.md\n\n{block}\n"
    return f"{existing.rstrip()}\n\n{block}\n"


def extract_block(text: str) -> str | None:
    """The managed region of ``text``, or ``None`` where it carries no markers."""
    start = text.find(BEGIN_MARKER)
    end = text.find(END_MARKER)
    if start == -1 or end == -1 or end < start:
        return None
    return text[start : end + len(END_MARKER)]


def divergences(checkout: Path) -> list[Divergence]:
    """Everything about ``checkout``'s agent layer that does not match this repository."""
    found: list[Divergence] = []

    agents = checkout / "AGENTS.md"
    if not agents.exists():
        found.append(Divergence("AGENTS.md", "missing"))
    else:
        block = extract_block(agents.read_text())
        if block is None:
            found.append(Divergence("AGENTS.md", "carries no edullm:begin/edullm:end markers"))
        elif block != expected_agents_block():
            diff = "\n".join(
                difflib.unified_diff(
                    expected_agents_block().splitlines(),
                    block.splitlines(),
                    "platform",
                    "checkout",
                    lineterm="",
                )
            )
            found.append(Divergence("AGENTS.md", diff))

    claude = checkout / "CLAUDE.md"
    if not claude.exists():
        found.append(Divergence("CLAUDE.md", "missing, so Claude Code reads nothing at all"))
    elif CLAUDE_IMPORT not in claude.read_text():
        found.append(
            Divergence(
                "CLAUDE.md",
                f"does not carry `{CLAUDE_IMPORT}`, so Claude Code reads this repository's "
                "own guidance and never reaches the rule in AGENTS.md",
            )
        )

    return found


def write_into(checkout: Path) -> list[str]:
    """Write the layout into ``checkout``. Returns the paths that changed."""
    changed: list[str] = []

    agents = checkout / "AGENTS.md"
    existing = agents.read_text() if agents.exists() else ""
    spliced = splice_block(existing, expected_agents_block())
    if spliced != existing:
        agents.write_text(spliced)
        changed.append("AGENTS.md")

    claude = checkout / "CLAUDE.md"
    if not claude.exists():
        claude.write_text(CLAUDE_BRIDGE)
        changed.append("CLAUDE.md")
    elif CLAUDE_IMPORT not in claude.read_text():
        # At the top, because Claude Code resolves imports in order and the rule is the
        # thing that has to be true before anything else in the file makes sense. Prepended
        # rather than spliced into a marked region: this is one line, it is not going to
        # change, and a marker pair around it would be more machinery than the line it holds.
        claude.write_text(f"{CLAUDE_IMPORT}\n\n{claude.read_text()}")
        changed.append("CLAUDE.md")

    return changed


def build_parser() -> argparse.ArgumentParser:
    """The parser, at module scope because that is what holds the workflow to it.

    ``tests/test_workflow_tool_arguments.py`` imports this by name and compares the flags it
    accepts with the flags every workflow passes it, in both directions. A parser built
    inside ``main`` is invisible to that and the pair drifts silently, which is the same
    failure this whole module exists to stop, one level up.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("checkout", type=Path, help="a research checkout to write into")
    parser.add_argument(
        "--check",
        action="store_true",
        help="report what has drifted and write nothing, exiting 1 where anything has",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    checkout: Path = args.checkout.resolve()
    if not (checkout / ".git").exists():
        parser.error(f"{checkout} is not a git checkout")

    if args.check:
        found = divergences(checkout)
        for divergence in found:
            print(f"{divergence.path}: {divergence.detail}")
        if found:
            print(f"\n{len(found)} path(s) diverge from {PROJECT_ROOT}", file=sys.stderr)
            return 1
        print("the agent layer in this checkout matches the platform")
        return 0

    changed = write_into(checkout)
    for path in changed:
        print(f"wrote {path}")
    if not changed:
        print("nothing to do; this checkout was already current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
