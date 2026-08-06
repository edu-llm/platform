"""One person's preference on one laptop, kept structurally apart from reviewed policy.

**THIS MODULE EXISTS TO BE A SECOND LOADER, AND THAT IS THE WHOLE OF THE DESIGN.** The six
files ``cli.configuration`` opens are policy: shared, reviewed in a pull request, travelling
inside the wheel, identical for every install. What is read here is a preference: one line,
one laptop, nobody else's business, and no review. The two must never load through the same
path, because a single loader is how a threshold ends up in somebody's home directory and how
a preference ends up in ``config/``. So nothing here returns a
:class:`~edullm_platform.cli.configuration.ReviewedConfiguration`, nothing here is a field on
one, and :class:`ConfigurationUnreadableError` is not raised from this file. The separation is
the file boundary rather than a comment asking people to be careful.

**A FILE RATHER THAN AN ENVIRONMENT VARIABLE, AND THE VARIABLE IS THE WORSE OF THE TWO.**
``EDULLM_CONFIG_DIR`` is the precedent for a variable and it is a precedent for the wrong
thing: it points at reviewed configuration, so ``EDULLM_TEAM`` beside it would read as the
same class of input and invite exactly the confusion above. It is also invisible. A variable
exported months ago in a shell profile is not in any transcript, is not in any directory
listing, and is inherited by every agent, editor and CI shell started from that session, so
the run it silently charges to a group is charged by something nobody can see. A file has an
address that a refusal can print and that a person can open, and it survives a new shell,
which is the whole point of setting a default once.

**AND IT IS SET BY EDITING IT, WITH NO VERB BEHIND IT.** The verb set is five and the
population that meets ``team_is_ambiguous`` is exactly the population that is on two declared
groups, which is nobody's first week. A sixth verb would be a table entry, a help page, a
guide paragraph and a maintained surface, bought for people who are already comfortable
writing a word into a file the refusal names for them.

WHERE IT GOES IS ``gh``'S OWN RULE, SPELLED THE WAY THIS PACKAGE ALREADY SPELLS IT.
``XDG_CONFIG_HOME`` when it is set and ``~/.config`` when it is not, which is the first two
and the last of the four steps :func:`~edullm_platform.cli.workspace.gh_config_directory`
takes for ``hosts.yml`` and is what ``gh`` itself does on macOS as well as on Linux. One
rule, so macOS, Linux and WSL put the file in the same place and a researcher who moves
between them types the same path. Nothing new is imported for it.

**THE STEP NOT COPIED IS THE WINDOWS ONE, AND LEAVING IT OUT IS THE DECISION RATHER THAN THE
OVERSIGHT.** ``gh`` falls back to ``%AppData%\\GitHub CLI`` on Windows because ``gh`` has to
find a file it wrote itself; this file is written by a person, once, at a path a refusal
prints for them in full. On Windows with no ``XDG_CONFIG_HOME`` it lands under
``%USERPROFILE%\\.config\\edullm\\team``, which is readable, writable and named in the
refusal that recommends it. A second convention here would buy a tidier path and cost a
second place to look for a file that is already hard enough to remember.

**A FILE THAT WILL NOT READ IS NOT AN ERROR AND CANNOT MISATTRIBUTE ANYTHING.** An
unreadable or empty file answers "no default", which falls back to the roster, which either
resolves to a group the submitter is on or refuses as ``team_is_ambiguous`` naming this path.
Neither outcome charges the run to some other group, which is the only failure worth being
loud about, and the refusal is where a person is told the file is there to look at.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "CONFIG_HOME_VARIABLE",
    "DEFAULT_TEAM_FILE",
    "PREFERENCES_DIRECTORY",
    "DefaultTeam",
    "default_team_file",
    "read_default_team",
]

#: The variable that moves a whole config home, read rather than owned. Set by the
#: freedesktop convention on Linux and honoured by ``gh`` on macOS too, which is what makes
#: this one rule rather than three.
CONFIG_HOME_VARIABLE = "XDG_CONFIG_HOME"

#: This tool's directory under the config home, named after the binary the way every other
#: tool's is.
PREFERENCES_DIRECTORY = "edullm"

#: The whole file name, and the whole format. A file called ``team`` holding a team id needs
#: no parser, no key, no quoting rule and no answer to "what else may I put in here" -- which
#: is the question that turns a preference into a second configuration file. A second
#: preference, if one is ever wanted, is a second decision rather than a line somebody adds.
#:
#: **AND THE DIRECTORY IS NOT EMPTY ON THE MACHINE THIS FEATURE IS FOR.** An earlier ORCD-era
#: ``edullm`` already keeps a ``config.yaml``, a lock file beside it and a ``recovery/``
#: directory under this same path on the owner's laptop. Nothing here reads any of them and
#: nothing there reads this. A preference spelled as a key in a shared ``config.yaml`` would
#: have landed inside a file another program owns, locks and rewrites; a file whose whole name
#: is the preference cannot collide with one.
DEFAULT_TEAM_FILE = "team"


@dataclass(frozen=True)
class DefaultTeam:
    """Where the preference lives, and what it says, which is often nothing.

    The path is carried even when the file is absent, deliberately. The reader who most needs
    the address is the one meeting ``team_is_ambiguous``, and at that moment the file does not
    exist yet -- so a type that could only describe a file that is already there would leave
    the refusal unable to name the thing it is recommending.
    """

    path: Path
    team: str | None


def default_team_file(
    *, environ: Mapping[str, str] | None = None, home: Path | None = None
) -> Path | None:
    """Where this machine keeps the preference, or nothing where there is no home to keep it in.

    ``home`` is for the suite and for nothing else. ``Path.home()`` reads the real one, and a
    test that read the real one would pass or fail depending on whether the person running it
    had set a default of their own.
    """
    variables = os.environ if environ is None else environ
    declared = variables.get(CONFIG_HOME_VARIABLE, "").strip()
    if declared:
        base = Path(declared)
    elif home is not None:
        base = home / ".config"
    else:
        try:
            base = Path.home() / ".config"
        except RuntimeError:
            # No home directory anybody can name, which is a container with no passwd entry
            # and no HOME. There is nowhere to have put a preference, so there is no
            # preference, and the roster answers as it did before this file existed.
            return None
    return base / PREFERENCES_DIRECTORY / DEFAULT_TEAM_FILE


def read_default_team(
    *, environ: Mapping[str, str] | None = None, home: Path | None = None
) -> DefaultTeam | None:
    """The team this person defaults to, beside the path it would have been read from.

    Not validated here, and that is the property the whole feature rests on. Whatever word is
    in the file is handed to the same checks a typed ``--team`` goes through, so a default
    naming a group that does not exist is refused as ``unregistered_team`` and one naming a
    group the roster does not put the submitter on is refused as
    ``submitter_not_in_claimed_team``. A preference that validated itself here would be a
    second opinion about team membership, and the direction it would fail is the one that
    matters: it would be a way of arriving at a team without meeting the checks a typed one
    meets.
    """
    path = default_team_file(environ=environ, home=home)
    if path is None:
        return None
    return DefaultTeam(path=path, team=_first_line(path))


def _first_line(path: Path) -> str | None:
    """The first line with anything on it, stripped, or nothing at all.

    A line rather than the whole file, so that a trailing newline, a blank line and a second
    line somebody left behind all read the same way as the file they meant to write. Anything
    below the first line is ignored rather than joined to it, because a team id with a newline
    in the middle would reach a refusal and be printed back at a reader in two pieces.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None
