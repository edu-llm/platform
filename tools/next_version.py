"""Read ``project.version``, and write the next version back over it.

WHY A TOOL RATHER THAN A SED LINE IN THE WORKFLOW. The version this prints becomes a tag,
a GitHub release, and the string every installed CLI compares itself against, so a
malformed answer is not a broken build -- it is a release nobody's staleness check can
parse, which fails silent in the direction of "you are current". Here it can be tested,
and ``tests/test_next_version.py`` is where the cases live.

**AND IT MOVES ``uv.lock`` IN THE SAME BREATH, WHICH IS NOT A TIDINESS.** The lock records
the root distribution's own version, ``uv sync --locked`` fails when that disagrees with
``pyproject.toml``, and that command is the first step of every CI job here. A bump that
wrote only ``pyproject.toml`` would go red on the first line CI runs, which is the state
this repository was already in once when ``project.version`` first moved off ``0.1.0``. So
the two files move together or neither does.

The lock is edited by anchored substitution rather than by running ``uv lock``, for one
reason beyond keeping this stdlib-only: a resolver would have to reach every dependency
including the git ones, and finding out that an unrelated upstream moved is not what
somebody bumping a version asked for. ``uv lock`` writes exactly the line this writes --
verified by running it -- and this cannot write anything else.

**WHO RUNS THIS, AND WHY IT IS NOT A WORKFLOW ANY MORE.** ``release-tag.yml`` used to call
this on every qualifying merge and push the result straight to ``main``. It could not:
branch protection refuses a push to ``main``, in as many words, and five merges in a row
failed on that line while ``releases/latest`` went on naming a tag from before all of them.
The version is a literal in a file, so only a commit can move it and only a pull request
can put a commit on ``main`` -- which makes this a command a person runs on a branch, and
the bump something a reviewer sees. ``ci.yml`` fails a pull request that changes what an
installed CLI answers while leaving ``project.version`` at a version already released, so
running this is not something anybody has to remember.

**ALL THREE SIZES, BECAUSE FOR A YEAR ONLY ONE OF THEM WAS REACHABLE.** This wrote the patch
component and nothing else, and the workflow that called it computed the next patch on every
qualifying merge, so a minor was a thing somebody had to hand-edit past a bot that would
overwrite it. #199 added a refusal that stops a submission which used to go through, which
the house standard calls a minor in as many words, and it was released as ``0.2.0`` with
everything else. ``--bump minor`` and ``--bump major`` are the whole of the fix: the size is
an argument the author of the change chooses, it lands as a reviewed line in the diff, and
``ci.yml`` refuses a change that declares no size at all rather than picking one for them.

**AND THEN THE THREE SIZES BECAME A MENU, WHICH IS THE CORRECTION THIS FILE CARRIES NOW.**
Offering ``patch``, ``minor`` and ``major`` as three peers, in that order, in every failure
message and in the pull request template, taught everybody that the size is a description to
be picked rather than a claim to be earned. In the twenty hours to 2026-08-06T01:24Z the version
went from ``0.2.2`` to ``3.2.0`` across twenty-six bumps: nine patches, fourteen minors and three
majors, against a repository nobody had yet been shown to have installed. Two of the three majors
carry no Break section in their published note, which by the house standard's own definition means
nothing broke and they were not majors.

The count moved twice while this was being written, which is the argument rather than a footnote
about it. Do not update these numbers as they drift. They are what one evening looked like.

So the sizes are no longer peers. **A patch is the default and it is what almost every change
here is.** A bare ``--bump`` is a patch, ``--bump patch`` is a patch, and neither is asked for
anything further. Anything wider requires ``--why`` and a sentence, and the sentence is not
decoration: it is written into ``pyproject.toml`` above the version so a reviewer reads it in
the diff, and ``release-tag.yml`` publishes it as the Break section of a major's release note
or the Summary of a minor's. That closes the same gap in two places, because a note that says
nothing a reader would act on is the same disease as a version that says a break happened
when none did.

This cannot tell a good reason from a bad one and does not try. What it can do is make the
sentence exist, make it reviewable, and make writing a bad one cost the same as writing a
true one, which is what turns the size back into a claim somebody made.

**AND NOW THERE IS A CEILING, BECAUSE THAT REPAIR WAS NOT THE WHOLE OF IT EITHER.** It
worked on the thing it was aimed at: of the forty-five releases cut after it, thirty-seven
are patches, and every one of the eight wider ones carries a sentence describing a real
change to what an installed CLI does. Nobody misclassified anything. The version still went
``1.3.6`` to ``4.2.1`` overnight, because forty-seven changes to the CLI merged in nineteen
hours and eight of them genuinely earned more than a patch.

So the ceiling is not a correction to the sizes. It is a separate rule about one of them:
a major is the only step that cannot be walked back for somebody who already installed this,
and the platform holds still on major :data:`MAJOR_CEILING` while people are learning to use
it. Earning a major and being allowed to take one are now different questions, and the
second is answered by a constant in this file rather than by an argument to this command.

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
    "MAJOR_CEILING",
    "MINIMUM_REASON_CHARACTERS",
    "REASON_PATTERN",
    "SIZES",
    "VERSION_PATTERN",
    "WIDER_THAN_A_PATCH",
    "MajorCeilingError",
    "build_parser",
    "checked_ceiling",
    "checked_reason",
    "lock_version_pattern",
    "next_patch_version",
    "next_version",
    "read_lock_version",
    "read_name",
    "read_reason",
    "read_version",
    "rewrite_lock_version",
    "rewrite_pinned_tag",
    "rewrite_reason",
    "rewrite_version",
]

#: The three statements a version bump can make, in the order they widen. Written here
#: rather than in ``build_parser`` so that ``ci.yml``, which asks this tool what each of them
#: would produce, iterates the same three names argparse accepts.
#:
#: ``major`` stays on this list while :data:`MAJOR_CEILING` holds, and that is the point
#: rather than an oversight: dropping it would make ``--bump major`` an argparse "invalid
#: choice", which tells a reader the word is unknown here. It is known, it is refused, and
#: the refusal is the whole message.
SIZES = ("patch", "minor", "major")

#: The size nothing has to argue for, because it is what almost every change here is.
DEFAULT_SIZE = "patch"

#: The two that do. Derived from :data:`SIZES` rather than written out, so a fourth size
#: would need a decision about it here rather than defaulting into the unjustified half.
WIDER_THAN_A_PATCH = tuple(size for size in SIZES if size != DEFAULT_SIZE)

#: THE MAJOR THIS REPOSITORY STAYS ON, AND THE ONE PLACE THAT DECIDES IT.
#:
#: **This is the constant to change if the answer is genuinely a version 5.** Change it here,
#: in a pull request, with a reviewer, and everything below follows: the arithmetic, the
#: refusals, the two workflows that ask this tool what a bump would produce, and
#: ``tests/test_next_version.py``, which asserts the declared version sits on this number.
#: There is no flag for it and there is deliberately not going to be one -- a ceiling a
#: caller can pass its way past is a suggestion, and this is the second time the version has
#: been asked to stop climbing.
#:
#: WHY A CEILING AND NOT MORE ADVICE. The version went from ``1.3.6`` to ``4.2.1`` overnight.
#: The first repair made a patch the default and made anything wider write a sentence, and it
#: worked: of the forty-five releases cut after it, thirty-seven are patches and every one of
#: the eight wider ones carries a true sentence about a real change. ``4.0.0`` was earned --
#: ``edullm status`` began printing ``ADMITTED`` where it printed ``SUBMITTED``. So this
#: number is not here because somebody was careless with the last one. It is here because a
#: major is the one step that cannot be walked back for anybody who has this installed, and
#: the owner has decided the platform holds still on 4 while people are learning to use it.
#: Earning a major and taking one are now different things.
MAJOR_CEILING = 4

#: How much of a sentence counts as one. A floor rather than a judgement: this cannot tell
#: "adds a --since flag to status" from "improvements", and pretending otherwise would make
#: it a check that refuses good input. What it stops is the empty string and the single word
#: typed to get past an argument, which is the whole of what a bare requirement would buy.
MINIMUM_REASON_CHARACTERS = 12

#: ``version = "0.2.0"`` at the start of a line, which is the only place hatchling reads it
#: from and the only line this may touch. Anchored to the line start so a version inside a
#: dependency specifier or a comment cannot match.
VERSION_PATTERN = re.compile(r'^version\s*=\s*"(?P<version>[^"]+)"\s*$', re.MULTILINE)

#: Three dotted integers and nothing else. Narrower than PEP 440 on purpose: the tag is
#: ``v`` plus this, and a release tag with a local version or a pre-release segment in it
#: is a comparison the CLI's probe would have to be taught, for a release nobody here cuts.
#:
#: No leading zero on any component, which is semantic versioning's own rule and is load
#: bearing here rather than pedantry. ``\d+`` accepts ``2026.08.04``, and the next patch of
#: that is ``2026.8.5`` -- a version this tool invented, that is not the one it was handed,
#: and that nothing downstream would question. Reachable from a shell now that ``--of``
#: takes a version off a command line rather than out of a parsed file.
SEMANTIC_VERSION = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)$"
)

#: The line that says why the version above a patch is above a patch, as it sits in
#: ``pyproject.toml`` directly above the version it explains.
#:
#: A comment rather than a table entry, for the same reason the version is rewritten by
#: anchored substitution rather than by a TOML writer: a real key would need a writer, a
#: writer reformats a file that is nine tenths comment, and a key under ``[project]`` that
#: is not a ``project`` key is a warning from every tool that validates the schema. A comment
#: is invisible to ``tomllib``, invisible to hatchling, invisible to ``uv sync --locked``,
#: and perfectly visible in the diff a reviewer reads, which is the whole audience.
REASON_PATTERN = re.compile(
    r"# WHY THIS IS A (?P<size>MINOR|MAJOR) RATHER THAN A PATCH\. (?P<reason>\S.*)"
)


class VersionUnreadableError(RuntimeError):
    """``project.version`` is absent, malformed, or not where the substitution can reach."""


class MajorCeilingError(VersionUnreadableError):
    """A version above :data:`MAJOR_CEILING` was asked for, computed, or found on disk.

    A subclass rather than a sibling so that every ``except VersionUnreadableError`` already
    in this file keeps working: this arrives at the same place, prints its own message, and
    exits 2 like every other refusal here. It is its own type because a test that asserts
    the ceiling refuses should not pass when the version was merely malformed.
    """


def checked_ceiling(version: str) -> str:
    """``version`` back, or a refusal naming the constant that would let it through.

    Called on every version this tool would *produce* and on the one it reads off disk, so
    that the ceiling is not a property of the ``--bump major`` arithmetic alone. A hand-edited
    ``5.0.0`` in ``pyproject.toml`` reaches ``release-tag.yml`` as the string it is about to
    tag, and that step asks this tool for it -- so refusing here stops the tag as well as the
    bump.
    """
    matched = SEMANTIC_VERSION.fullmatch(version)
    if matched is None or int(matched["major"]) <= MAJOR_CEILING:
        return version
    raise MajorCeilingError(
        f"{version} is major {matched['major']} and this repository is held to major "
        f"{MAJOR_CEILING}. Nothing here will produce it and nothing here will tag it.\n\n"
        "THIS IS A DECISION AND NOT A BUG. A major says something that used to work has "
        "stopped, and every install in the field has to be re-installed before it is right "
        "again. The version went from 1.3.6 to 4.2.1 in one night and the owner has ruled "
        f"that it holds on {MAJOR_CEILING} for the foreseeable future.\n\n"
        "IF THE BREAK IS REAL, IT IS STILL A MINOR THIS WEEK. A new refusal, a new flag, a "
        "renamed value that the old spelling still answers to -- all of those are a minor, "
        "and the sentence you would have written for the major goes in the --why.\n\n"
        f"IF IT GENUINELY HAS TO BE MAJOR {MAJOR_CEILING + 1}, lift the ceiling where it is "
        "declared, in a pull request somebody reviews:\n\n"
        f"    MAJOR_CEILING in tools/next_version.py   (currently {MAJOR_CEILING})\n\n"
        "That is one line, it is reviewed, and it is on the record. A flag on this command "
        "would have been none of those things, which is why there is not one."
    )


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
    about. A bump that moved the declaration and left the pin would leave the pull request
    making it red on a test about a line nobody edited, which is a confusing half hour;
    moving both here is what keeps the bump a one-command change.

    Substituting the pin rather than regenerating the line, because regenerating it means
    knowing how it is spelled, and there is exactly one place that knows: this runs from a
    bare checkout with nothing installed, so it cannot ask.
    """
    return text.replace(f"@v{was}", f"@v{now}")


def read_reason(text: str) -> tuple[str, str] | None:
    """The size the file claims to be above a patch, and the sentence claiming it.

    ``None`` when there is no such line, which is what a patch leaves behind and therefore
    the ordinary state of this file. Read line by line rather than with a multiline pattern
    so that a version of this string quoted inside a longer comment cannot be mistaken for
    the declaration itself.
    """
    for line in text.splitlines():
        found = REASON_PATTERN.fullmatch(line.strip())
        if found is not None:
            return found["size"].lower(), found["reason"].strip()
    return None


def rewrite_reason(text: str, *, size: str, reason: str | None) -> str:
    """Put the reason above the version line, or take away the one that is there.

    **A PATCH CLEARS IT, WHICH IS THE HALF THAT KEEPS THIS HONEST.** The line describes the
    step the declared version takes, so a reason left behind by the previous minor sitting
    above a patch version is a file that says two different things, and the thing that reads
    it is a workflow deciding what goes in a published note. Every bump rewrites it,
    including the bump that rewrites it to nothing.
    """
    lines = [
        line
        for line in text.splitlines(keepends=True)
        if REASON_PATTERN.fullmatch(line.strip()) is None
    ]
    if reason is None:
        return "".join(lines)
    declared = f"# WHY THIS IS A {size.upper()} RATHER THAN A PATCH. {reason}\n"
    for index, line in enumerate(lines):
        if VERSION_PATTERN.fullmatch(line.rstrip("\r\n")) is not None:
            return "".join([*lines[:index], declared, *lines[index:]])
    raise VersionUnreadableError(
        "there is no top-level version line for the reason to sit above, so nothing here "
        "can record why this bump is wider than a patch"
    )


def checked_reason(size: str, reason: str | None) -> str | None:
    """What goes in the file for a bump of this size, or a refusal naming what is missing.

    ``None`` for a patch, which is the answer almost every change gets and the only one
    that needs no argument.
    """
    normalized = " ".join(reason.split()) if reason is not None else ""
    if size == DEFAULT_SIZE:
        if normalized:
            raise VersionUnreadableError(
                "a patch takes no --why. A patch is anything a re-install fixes, which is "
                "the great majority of what merges here, and it carries no human section in "
                "its release note. Drop --why, or say which of the two wider sizes this is:"
                "\n\n" + _sizes_said()
            )
        return None
    if not normalized:
        raise VersionUnreadableError(
            f"--bump {size} publishes a version that tells everybody something changed for "
            "them, and this refuses it without a sentence saying what.\n\n"
            "Patch is the default and is what almost every change here is. If a re-install "
            "fixes it, it is a patch and it needs no reason.\n\n" + _sizes_said() + "\n"
            "What you write is committed to pyproject.toml above the version, so a reviewer "
            "reads it in the diff, and release-tag.yml publishes it in the release note. "
            "Write the sentence a researcher needs rather than the one that gets past this."
        )
    if len(normalized) < MINIMUM_REASON_CHARACTERS:
        raise VersionUnreadableError(
            f"{normalized!r} is {len(normalized)} characters and this wants at least "
            f"{MINIMUM_REASON_CHARACTERS}. The sentence is published in the release note "
            "that thirty-five installs read after being told to re-install, so it has to "
            "say what changed for them."
        )
    if "\u2014" in normalized:
        raise VersionUnreadableError(
            "the house standard bans the em dash and this sentence is published prose. Use "
            "a full stop."
        )
    return normalized


def _sizes_said() -> str:
    """The three sizes with the command for each, in the order they should be reached for.

    One copy, because this is quoted by every refusal in this file and a second copy is how
    the patch line goes on saying it is one of three equals after this one stopped.
    """
    return (
        "  A patch is anything a re-install fixes, including a config addition and a\n"
        "  reworded refusal. It is the default and it needs no reason.\n"
        "      tools/next_version.py --bump patch\n"
        "\n"
        "  A minor is a new command, a new flag, a new optional spec field, or a new\n"
        "  refusal that can stop a submission which used to go through. It is also where\n"
        "  a break goes while the ceiling holds, with the same sentence in its --why.\n"
        '      tools/next_version.py --bump minor --why "status takes --since"\n'
        "\n"
        f"  A major is capped: nothing here produces a version above major "
        f"{MAJOR_CEILING},\n"
        "  and MAJOR_CEILING in tools/next_version.py is the one line that lifts it.\n"
    )


def next_version(version: str, size: str = DEFAULT_SIZE) -> str:
    """``0.2.0`` to ``0.2.1``, ``0.3.0`` or ``1.0.0``. Refuses anything ambiguous.

    A wider bump zeroes everything below it, which is the half of semantic versioning that
    is easy to get wrong by hand: ``0.2.1`` to ``0.3.1`` reads as a minor and sorts as one,
    and the tag it produces is one nobody can explain a year later.
    """
    if size not in SIZES:
        raise VersionUnreadableError(f"{size!r} is not one of {', '.join(SIZES)}")
    matched = SEMANTIC_VERSION.fullmatch(version)
    if matched is None:
        raise VersionUnreadableError(
            f"{version!r} is not major.minor.patch, so there is no next {size} version. "
            "Releases here are tagged v<version> and compared as three integers."
        )
    major, minor, patch = (int(matched[part]) for part in ("major", "minor", "patch"))
    if size == "major":
        stepped = f"{major + 1}.0.0"
    elif size == "minor":
        stepped = f"{major}.{minor + 1}.0"
    else:
        stepped = f"{major}.{minor}.{patch + 1}"
    # THE CEILING IS ON WHAT COMES OUT, NOT ON THE WORD "MAJOR". A patch of 5.0.0 is 5.0.1,
    # which is still a version this repository has decided not to have, and checking the size
    # rather than the answer would let it through the one time it mattered.
    return checked_ceiling(stepped)


def next_patch_version(version: str) -> str:
    """``0.2.0`` to ``0.2.1``. Kept as its own name because it is the overwhelming case."""
    return next_version(version, "patch")


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


def build_parser() -> argparse.ArgumentParser:
    """Named this because ``tests/test_workflow_tool_arguments.py`` looks for the name.

    That module builds the parser of every tool a workflow runs and checks the flags the
    workflow passes against it, and it finds the parser by calling ``build_parser``. Built
    inside ``main`` this parser was invisible to it, and the invocation that has been in
    ``release-tag.yml`` since it was written went unchecked for the same reason -- it was
    spelled ``python3``, which that module's pattern does not match either. Both are the
    kind of gap whose first symptom is a workflow failing at argparse in a job that has
    already done something.
    """
    parser = argparse.ArgumentParser(
        description="Print project.version, the version a bump of a given size would "
        "produce, or write that version back over it."
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
        nargs="?",
        const=DEFAULT_SIZE,
        choices=SIZES,
        default=None,
        help="write the next version of this size back to the file before printing it; "
        f"a bare --bump is a {DEFAULT_SIZE}, anything wider needs --why, and major is "
        f"refused while MAJOR_CEILING in this file holds at {MAJOR_CEILING}",
    )
    parser.add_argument(
        "--why",
        default=None,
        help="one sentence saying what a "
        + " or a ".join(WIDER_THAN_A_PATCH)
        + " changes for somebody who has this installed. Required for those two, refused "
        "for a patch, committed above the version line and published in the release note",
    )
    parser.add_argument(
        "--ceiling",
        action="store_true",
        help="print the major this repository is held to, and read and write nothing. For "
        "the tag guard, which has to name the number without keeping a second copy of it",
    )
    parser.add_argument(
        "--show-why",
        action="store_true",
        help="print the declared size and reason as one line, and write nothing; prints "
        "nothing at all when the declared version is a patch",
    )
    parser.add_argument(
        "--next",
        choices=SIZES,
        default=None,
        dest="next_size",
        help="print the version a bump of this size would produce, and write nothing",
    )
    parser.add_argument(
        "--of",
        default=None,
        help="the version --next steps from; defaults to the declared one",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    # FIRST, BECAUSE IT ANSWERS FROM A CONSTANT AND TOUCHES NO FILE. `refuse-a-tag-above-the-
    # ceiling.yml` asks this about a tag that may point anywhere, so it must not depend on a
    # readable pyproject.toml, and it must not be a second copy of the number.
    if arguments.ceiling:
        print(MAJOR_CEILING)
        return 0
    # A --why with nothing to attach it to, and a --show-why that would swallow a bump. Both
    # are refusals rather than shrugs, because both are somebody believing they have recorded
    # a reason or made a release, and the quiet version of either is found out at the tag.
    if arguments.why is not None and arguments.bump is None:
        print(
            "--why records the reason for a bump and there is no --bump here, so there is "
            "nothing to record it against.",
            file=sys.stderr,
        )
        return 2
    if arguments.show_why and (arguments.bump is not None or arguments.next_size is not None):
        print(
            "--show-why reads and writes nothing, so it cannot be combined with --bump or "
            "--next. Run it on its own.",
            file=sys.stderr,
        )
        return 2
    lock_path = (
        arguments.lock if arguments.lock is not None else arguments.pyproject.parent / "uv.lock"
    )
    # --next --of asks a question about a version somebody names, which is how `ci.yml` finds
    # out what each of the three sizes would produce from the latest release. Nothing is read
    # off disk for it, because the latest release is a tag and not a file in this checkout.
    if arguments.next_size is not None and arguments.of is not None:
        try:
            print(next_version(arguments.of, arguments.next_size))
        except VersionUnreadableError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        return 0
    text = arguments.pyproject.read_text(encoding="utf-8")
    # WHY THIS IS ABOVE THE PARSE OF THE VERSION AND NOT BESIDE IT. `release-tag.yml` asks
    # this on a merge whose version has already been read and validated by the step above,
    # and what it wants is the sentence, not a second opinion on the number. A file with an
    # unreadable version and a readable reason is not a state this should invent an error
    # for, because the error that matters has already been raised by then.
    if arguments.show_why:
        declared = read_reason(text)
        if declared is not None:
            print(f"{declared[0]} {declared[1]}")
        return 0
    try:
        # THE DECLARED VERSION MEETS THE CEILING TOO, WHICH IS THE HALF THAT COVERS THE HAND
        # EDIT. Nothing makes anybody use this tool to move three integers in a text file,
        # and `release-tag.yml` tags whatever a bare run of this prints. So a 5.0.0 that was
        # typed rather than computed still stops here, one step before it becomes a tag.
        version = checked_ceiling(read_version(text))
        if arguments.next_size is not None:
            print(next_version(version, arguments.next_size))
            return 0
        if arguments.bump is not None:
            # THE CEILING BEFORE THE SENTENCE, because the sentence is work and the ceiling
            # is a wall. Asking somebody to write a publishable line about what they broke
            # and then telling them the version was never available is the order that makes
            # a deliberate refusal feel like a bug.
            was, version = version, next_version(version, arguments.bump)
            # AND THEN THE ONE THAT REFUSES ON PURPOSE. A missing --why on a minor is an
            # argument error rather than a broken file, and the reader has to meet it before
            # a version they cannot have. Nothing is written until both have passed.
            reason = checked_reason(arguments.bump, arguments.why)
            # BOTH REWRITES ARE COMPUTED BEFORE EITHER IS WRITTEN. A half-applied bump is a
            # tree whose two files disagree, which is precisely the state that fails
            # `uv sync --locked` -- so the failure mode of guarding against it must not be
            # the thing it guards against.
            #
            # The reason goes on last, after the pin rewrite, so that a sentence naming a
            # version cannot be rewritten by the substitution that moves the install line.
            rewritten = rewrite_reason(
                rewrite_pinned_tag(rewrite_version(text, version), was=was, now=version),
                size=arguments.bump,
                reason=reason,
            )
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
