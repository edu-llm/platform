"""Whether the tree this repository commits can be checked out on Windows at all.

**The failure this is written about.** ``uv tool install git+https://github.com/edu-llm/platform``
resolves a git dependency by shelling out to ``git``, and on Windows that is Git for Windows,
which refuses to write a path past 260 characters unless ``core.longPaths`` is set. It is off by
default and Git for Windows says why in its own release notes: many Windows programs, Explorer
among them, cannot handle the result. So a tracked path long enough puts the install out of reach
of a researcher, and the error names a file in a cache directory rather than anything about this
repository.

**IT IS INVISIBLE FROM THE MACHINE THAT WOULD CHECK IT, WHICH IS WHY IT IS A TEST.** The prefix
uv checks out under contains the researcher's Windows username, so the same tree installs for one
person and not for the next. On 2026-08-05 the longest tracked path here was 174 characters,
which cleared the limit by four characters for a five-character username and missed it by one for
a ten-character one. Nobody on macOS or Linux can reach that, no review would show it, and the
person who hits it is told ``Filename too long`` about a path they did not write. A bound checked
here turns that into a red check on the pull request that would have crossed it.

**The bound is arithmetic and not a round number.** :data:`LONGEST_TRACKED_PATH` is what is left
of the limit after the worst prefix a researcher can arrive with, and every term is below.

**A test that cannot fail is worth nothing.** :func:`test_a_path_over_the_bound_is_actually_caught`
plants an over-long path into the measurement and requires it to be named, and
:func:`test_the_measurement_reads_the_committed_tree` requires the enumeration to have found this
repository rather than an empty list -- which is the way a measurement of the wrong tree passes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Final

import pytest

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]

#: Windows' documented maximum path, in characters, including the terminating null. A path
#: may therefore be 259 characters of text.
#: https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation
WINDOWS_MAX_PATH: Final = 260

#: Everything in uv's git checkout prefix except the username, taken from the layout in a real
#: ``Filename too long`` report against uv, astral-sh/uv#12341:
#: ``C:\Users\<user>\AppData\Local\uv\cache\git-v0\checkouts\<16 hex>\<9 hex>\``.
#:
#:   ``C:\Users\``                                  9
#:   ``\AppData\Local\uv\cache\git-v0\checkouts\``  41
#:   ``<16 hex>\``                                  17
#:   ``<9 hex>\``                                   10
#:                                                 ---
#:                                                  77
#:
#: Checked against the two measured cases rather than trusted: a five-character username gives
#: 82 and a ten-character one 87, which is what those installs were observed to build.
CHECKOUT_PREFIX_WITHOUT_THE_USERNAME: Final = 77

#: The longest username Windows will create. ``sAMAccountName`` is 20 characters or fewer and
#: ``NetUserAdd`` documents the same limit for a local account, so this is the whole population
#: rather than a guess at a long name.
#: https://learn.microsoft.com/en-us/windows/win32/adschema/a-samaccountname
LONGEST_WINDOWS_USERNAME: Final = 20

#: What a tracked path may be. Not a round number and not the current maximum plus slack:
#: it is what the limit leaves once the worst prefix a researcher can arrive with is spent.
#:
#:   259  the limit, less its terminating null
#:  - 77  uv's checkout prefix, without the username
#:  - 20  the longest username Windows will create
#:   ---
#:   162
LONGEST_TRACKED_PATH: Final = (
    WINDOWS_MAX_PATH - 1 - CHECKOUT_PREFIX_WITHOUT_THE_USERNAME - LONGEST_WINDOWS_USERNAME
)


def tracked_paths(root: Path) -> tuple[str, ...]:
    """Every path this repository commits, repository-relative.

    ``git ls-files`` rather than a walk of the checkout, for the reason
    ``tests/test_committed_fixture_names.py`` gives beside its own: a push carries the commit
    and not the working tree, and an untracked file long enough to break an install is a file
    no install ever sees. Repository-relative is also exactly what gets appended to the
    checkout prefix, so the number measured here is the number that meets the limit.
    """
    listing = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout
    return tuple(path for path in listing.split("\0") if path)


def over_the_bound(paths: tuple[str, ...]) -> tuple[str, ...]:
    """The paths no bound leaves room for, longest first, so a failure names the worst one."""
    return tuple(
        sorted(
            (path for path in paths if len(path) > LONGEST_TRACKED_PATH),
            key=len,
            reverse=True,
        )
    )


def username_length_tolerated(longest: int) -> int:
    """How long a Windows username may be before this tree stops installing for its owner.

    The inverse of the bound, reported rather than derived in the reader's head, because the
    useful form of "the longest path is 174" is "this installs for Frank and not for Aryan".
    """
    return WINDOWS_MAX_PATH - 1 - CHECKOUT_PREFIX_WITHOUT_THE_USERNAME - longest


@pytest.fixture(scope="module")
def committed() -> tuple[str, ...]:
    return tracked_paths(PROJECT_ROOT)


@pytest.mark.slow
def test_the_measurement_reads_the_committed_tree(committed: tuple[str, ...]) -> None:
    """The enumeration found this repository, or every assertion below proved nothing.

    The way a path-length check passes for the wrong reason is by measuring an empty list or
    somebody else's tree, and both look identical to a clean run. Asserted separately from the
    bound so the two failures read differently.
    """
    assert committed
    assert "pyproject.toml" in committed
    assert not any(path.startswith("/") for path in committed)


@pytest.mark.slow
def test_no_tracked_path_would_break_a_windows_install(committed: tuple[str, ...]) -> None:
    too_long = over_the_bound(committed)
    longest = max(len(path) for path in committed)
    headline = (
        f"{len(too_long)} tracked path(s) are longer than {LONGEST_TRACKED_PATH} characters, "
        f"so uv tool install fails with 'Filename too long' for a Windows researcher. The "
        f"longest is {longest} characters, which installs only for a username of "
        f"{username_length_tolerated(longest)} characters or fewer:"
    )
    assert not too_long, "\n".join(
        [headline, *(f"  {len(path)}  {path}" for path in too_long[:10])]
    )


@pytest.mark.slow
def test_a_path_over_the_bound_is_actually_caught(committed: tuple[str, ...]) -> None:
    """One path a character too long, planted, and required to be named.

    A character over rather than obviously over, because the boundary is where an off-by-one
    in the comparison would live and the whole finding was decided by four characters. Both
    sides of it are asserted, so a check that caught everything would fail here too.
    """
    exactly_at_the_bound = "x" * LONGEST_TRACKED_PATH
    one_over = "x" * (LONGEST_TRACKED_PATH + 1)
    assert one_over in over_the_bound((*committed, one_over))
    assert exactly_at_the_bound not in over_the_bound((*committed, exactly_at_the_bound))


@pytest.mark.slow
def test_the_bound_leaves_the_username_population_room(committed: tuple[str, ...]) -> None:
    """The bound is stated as a length; what it buys is a set of people, so say that too.

    This is the assertion that would have been red on 2026-08-05, when the longest path was
    174 and the answer here was 8 -- a username of eight characters, against a limit Windows
    itself sets at twenty.
    """
    longest = max(len(path) for path in committed)
    assert username_length_tolerated(longest) >= LONGEST_WINDOWS_USERNAME
