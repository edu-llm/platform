"""The version bump that becomes a tag, a release, and every CLI's idea of "current".

A WRONG ANSWER HERE IS NOT A BROKEN BUILD, WHICH IS WHY IT IS TESTED AT ALL. What
``tools/next_version.py`` prints is pushed as a tag and published as a release, and
``edullm submit`` compares the installed version against that tag before it spends
somebody's approval. A malformed version is discarded by the CLI as unparseable, and a
discarded release reads as "there is nothing newer" -- so the failure is silent and points
the wrong way.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.next_version import (
    VersionUnreadableError,
    main,
    next_patch_version,
    next_version,
    read_lock_version,
    read_name,
    read_version,
    rewrite_lock_version,
    rewrite_version,
)

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
UV_LOCK = Path(__file__).resolve().parents[1] / "uv.lock"


def test_the_patch_component_is_what_moves() -> None:
    assert next_patch_version("0.2.0") == "0.2.1"
    assert next_patch_version("0.2.9") == "0.2.10"
    assert next_patch_version("1.0.0") == "1.0.1"


def test_a_wider_bump_zeroes_everything_below_it() -> None:
    """Mutation: increment the component and leave the ones under it.

    ``0.2.1`` to ``0.3.1`` reads as a minor, sorts as one, and is a tag nobody can account
    for a year later, because the seven patches it implies were never cut. This is the half
    of semantic versioning that is easy to get wrong by hand, which is the argument for the
    size being an argument to a tool rather than an edit to a line.
    """
    assert next_version("0.2.1", "minor") == "0.3.0"
    assert next_version("0.2.1", "major") == "1.0.0"
    assert next_version("1.4.7", "minor") == "1.5.0"
    assert next_version("1.4.7", "major") == "2.0.0"
    assert next_version("0.2.1") == "0.2.2", "a size nobody names is a patch"


def test_a_size_that_is_not_one_of_the_three_is_refused() -> None:
    """Mutation: fall through to a patch.

    ``--bump minro`` is caught by argparse, but the same string reaches here from
    ``ci.yml``'s loop over the sizes, and a typo that silently produced a patch would make
    the check accept a version nobody asked for and report it as the size they did ask for.
    """
    with pytest.raises(VersionUnreadableError):
        next_version("0.2.1", "enormous")


@pytest.mark.parametrize(
    "version",
    ["0.2", "0.2.0rc1", "0.2.0+local", "v0.2.0", "2026.08.04.1", "2026.08.04", "0.02.1"],
)
def test_a_version_no_tag_could_be_cut_from_is_refused(version: str) -> None:
    """Mutation: fall back to appending ``.1``, or to PEP 440's full grammar.

    The tag is ``v`` plus this string and the CLI parses it back as three integers. A
    pre-release or a local version would be tagged happily and then thrown away by every
    installed CLI as unparseable, which reads as "no newer release exists".
    """
    with pytest.raises(VersionUnreadableError):
        next_patch_version(version)


def test_the_version_is_parsed_as_toml_rather_than_matched_out_of_the_text() -> None:
    """Mutation: regex the file. ``pyproject.toml`` here is nine tenths comment.

    The comments in it quote versions -- ``0.1.0``, an install line pinned to a tag -- and
    a match-first reader picks up whichever appears earliest.
    """
    assert read_version(PYPROJECT.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "text",
    ["", "[project]\nname = 'x'\n", "[project]\nversion = 1\n", "[project\n"],
)
def test_a_file_with_no_usable_version_is_refused_rather_than_defaulted(text: str) -> None:
    with pytest.raises(VersionUnreadableError):
        read_version(text)


def test_rewriting_touches_the_version_line_and_leaves_the_comments() -> None:
    """Mutation: round-trip through a TOML writer.

    Every writer reformats, and this file's comments are most of its content -- the
    argument for the dev group, the argument for force-include, the install line itself.
    A release commit that silently deleted them would be a very expensive tidy-up.
    """
    original = PYPROJECT.read_text(encoding="utf-8")

    rewritten = rewrite_version(original, "9.9.9")

    assert read_version(rewritten) == "9.9.9"
    assert rewritten.count("\n") == original.count("\n")
    assert "THE BINARY." in rewritten
    assert "force-include" in rewritten


def test_two_version_lines_are_refused_rather_than_guessed_between() -> None:
    """``client/`` carries its own ``[project]`` table, and one day these files may merge.

    Mutation: substitute the first match. On a merged file that rewrites the client's
    version to the platform's, and the client is a separately versioned distribution that
    ships into a research image.
    """
    with pytest.raises(VersionUnreadableError):
        rewrite_version('version = "0.1.0"\nversion = "0.2.0"\n', "9.9.9")


def test_the_lock_agrees_with_the_version_it_locks() -> None:
    """THE ONE THAT WOULD HAVE CAUGHT IT. Mutation: bump pyproject.toml and nothing else.

    ``uv.lock`` records the root distribution's own version, and ``uv sync --locked`` --
    the first step of every job in ``ci.yml`` -- fails outright when that disagrees with
    ``pyproject.toml``. Nothing else in the suite reads the lock, so the whole of the
    evidence that the two are in step is this assertion.

    It is the check that was missing when ``project.version`` first moved off ``0.1.0``:
    the suite was green, ruff and mypy were clean, and the build would have failed on the
    first line CI ran.
    """
    assert read_lock_version(
        UV_LOCK.read_text(encoding="utf-8"),
        distribution=read_name(PYPROJECT.read_text(encoding="utf-8")),
    ) == read_version(PYPROJECT.read_text(encoding="utf-8"))


def test_the_lock_rewrite_moves_the_root_package_and_nothing_near_it() -> None:
    """Mutation: substitute on the version line alone.

    Forty-odd packages in that file have one. Matching the ``name``/``version`` pair is
    what makes the substitution name this project rather than whichever dependency uv
    happened to write first.
    """
    original = UV_LOCK.read_text(encoding="utf-8")
    distribution = read_name(PYPROJECT.read_text(encoding="utf-8"))
    was = read_lock_version(original, distribution=distribution)

    rewritten = rewrite_lock_version(original, distribution=distribution, version="9.9.9")

    assert read_lock_version(rewritten, distribution=distribution) == "9.9.9"
    assert rewritten.count("\n") == original.count("\n")
    assert len(rewritten) - len(original) == len("9.9.9") - len(was)


def test_a_lock_that_does_not_name_the_root_package_is_refused() -> None:
    """Mutation: fall through and write only pyproject.toml.

    Silently skipping the lock is the failure this whole path exists to prevent, so a lock
    the substitution cannot find its way around has to stop the bump rather than let half
    of it through.
    """
    with pytest.raises(VersionUnreadableError):
        rewrite_lock_version(
            'name = "something-else"\nversion = "1.0.0"\n', distribution="x", version="9.9.9"
        )


def test_the_read_a_workflow_makes_and_the_bump_a_person_makes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The two invocations, and they are made by different things now.

    ``release-tag.yml`` runs the read and nothing else: it tags what ``pyproject.toml``
    declares and has no way to write it, because writing it means a commit on ``main`` and
    branch protection allows those only through a pull request. The bump is the other
    invocation, run by whoever opens that pull request.

    That a read writes nothing is the load-bearing half here. A read with a side effect
    would have the tagging workflow modifying its own checkout on every merge, and the
    next thing it does is decide whether a tag matching that version already exists.
    """
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "x"\nversion = "0.2.0"\n', encoding="utf-8")
    lock = tmp_path / "uv.lock"
    lock.write_text('[[package]]\nname = "x"\nversion = "0.2.0"\n', encoding="utf-8")

    assert main(["--pyproject", str(pyproject)]) == 0
    assert capsys.readouterr().out == "0.2.0\n"
    assert read_version(pyproject.read_text(encoding="utf-8")) == "0.2.0"
    assert read_lock_version(lock.read_text(encoding="utf-8"), distribution="x") == "0.2.0"

    assert main(["--pyproject", str(pyproject), "--bump"]) == 0
    assert capsys.readouterr().out == "0.2.1\n"
    assert read_version(pyproject.read_text(encoding="utf-8")) == "0.2.1"
    assert read_lock_version(lock.read_text(encoding="utf-8"), distribution="x") == "0.2.1"


def test_asking_what_a_size_would_produce_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The invocation ``ci.yml`` makes three times per pull request that earns a release.

    It asks what each of the three sizes would produce from the latest release and compares
    the answers with what the branch declares. A question with a side effect would have a
    required check rewriting the tree it is checking, and the next thing that job does is
    hand that tree to nothing at all -- so the damage would first appear as a diff nobody
    can account for in whatever ran next.

    ``--of`` is what makes the question answerable at all. The latest release is a tag, not
    a line in this checkout, so the version the three sizes step from is named on the
    command line rather than read off disk.
    """
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "x"\nversion = "0.2.2"\n', encoding="utf-8")

    assert main(["--pyproject", str(pyproject), "--next", "minor"]) == 0
    assert capsys.readouterr().out == "0.3.0\n"

    assert main(["--pyproject", str(pyproject), "--next", "patch", "--of", "0.9.4"]) == 0
    assert capsys.readouterr().out == "0.9.5\n"

    assert read_version(pyproject.read_text(encoding="utf-8")) == "0.2.2"


def test_a_named_version_no_tag_could_be_cut_from_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: let ``--of`` through unparsed.

    What reaches it is a tag name with the ``v`` taken off by a shell parameter expansion,
    and a repository whose latest release is a date or a pre-release would have the check
    comparing against something that is not a version. Two, because the question is
    unanswerable rather than answered no.
    """
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "x"\nversion = "0.2.2"\n', encoding="utf-8")

    assert main(["--pyproject", str(pyproject), "--next", "patch", "--of", "2026.08.04"]) == 2
    assert capsys.readouterr().out == ""


def test_a_bump_names_the_size_it_makes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """WHAT #199 COULD NOT SAY. Mutation: ignore the argument and bump the patch.

    Only the patch component ever moved, because the workflow computed the next patch on
    every qualifying merge and would have walked over a hand-written minor. #199 added a
    refusal that stops a submission which used to go through, which the house standard calls
    a minor in as many words, and it shipped as part of a patch with sixty other merges.
    """
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "x"\nversion = "0.2.2"\n', encoding="utf-8")
    lock = tmp_path / "uv.lock"
    lock.write_text('[[package]]\nname = "x"\nversion = "0.2.2"\n', encoding="utf-8")

    assert main(["--pyproject", str(pyproject), "--bump", "minor"]) == 0
    assert capsys.readouterr().out == "0.3.0\n"
    assert read_version(pyproject.read_text(encoding="utf-8")) == "0.3.0"
    assert read_lock_version(lock.read_text(encoding="utf-8"), distribution="x") == "0.3.0"

    assert main(["--pyproject", str(pyproject), "--bump", "major"]) == 0
    assert capsys.readouterr().out == "1.0.0\n"
    assert read_version(pyproject.read_text(encoding="utf-8")) == "1.0.0"
    assert read_lock_version(lock.read_text(encoding="utf-8"), distribution="x") == "1.0.0"


def test_a_bump_of_the_real_files_leaves_a_tree_the_rest_of_the_suite_accepts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """THE WHOLE POINT, AND THE ONLY TEST THAT EXERCISES A BUMP ON THE ACTUAL FILES.

    Three files move on a bump and two of them are files nobody editing a version thinks
    about. ``uv.lock`` records the root version and ``uv sync --locked`` -- the first line
    of every CI job -- refuses a disagreement; ``tests/test_cli_install_command.py`` holds
    ``pyproject.toml``'s pinned install line to the declared version. Both used to break on
    a bump, and both are asserted here against copies of the real files, because the
    minimal fixtures above would keep passing if either coupling were reintroduced.

    A bump now lands in an ordinary pull request, so a tree this leaves half-written is
    caught by the same four gates as anything else rather than after the fact. That is the
    argument for this test being cheap rather than for it being unnecessary: what it
    protects is the one-command promise the failure message in ``ci.yml`` makes, and a
    command that needs a follow-up nobody mentioned is a command people stop trusting.
    """
    from edullm_platform.cli.actions import PLATFORM_REPOSITORY
    from edullm_platform.cli.release import install_command

    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(PYPROJECT.read_text(encoding="utf-8"), encoding="utf-8")
    lock = tmp_path / "uv.lock"
    lock.write_text(UV_LOCK.read_text(encoding="utf-8"), encoding="utf-8")
    distribution = read_name(pyproject.read_text(encoding="utf-8"))

    assert main(["--pyproject", str(pyproject), "--bump"]) == 0
    bumped = capsys.readouterr().out.strip()

    text = pyproject.read_text(encoding="utf-8")
    assert read_version(text) == bumped
    assert read_lock_version(lock.read_text(encoding="utf-8"), distribution=distribution) == bumped
    assert install_command(repository=PLATFORM_REPOSITORY, tag=f"v{bumped}") in text


def test_a_bump_the_lock_refuses_leaves_both_files_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: write pyproject.toml first and the lock second.

    A half-applied bump is a tree whose two files disagree, which is exactly the state
    ``uv sync --locked`` fails on -- so the guard must not be able to create it. Here the
    lock names a different distribution, the rewrite refuses, and the version on disk has
    to be the one that was there.
    """
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "x"\nversion = "0.2.0"\n', encoding="utf-8")
    lock = tmp_path / "uv.lock"
    lock.write_text('[[package]]\nname = "y"\nversion = "0.2.0"\n', encoding="utf-8")

    assert main(["--pyproject", str(pyproject), "--bump"]) == 2
    assert capsys.readouterr().out == ""
    assert read_version(pyproject.read_text(encoding="utf-8")) == "0.2.0"


def test_an_unusable_file_exits_two_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two, which is this repository's code for "nobody could judge this".

    Mutation: exit 1. The workflow would read that as an ordinary failure; the distinction
    the whole repository makes is between a verdict and an unanswerable question, and a
    release that cannot name its own version is the second.
    """
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "x"\nversion = "0.2.0rc1"\n', encoding="utf-8")

    assert main(["--pyproject", str(pyproject), "--bump"]) == 2
    assert capsys.readouterr().out == ""
    assert read_version(pyproject.read_text(encoding="utf-8")) == "0.2.0rc1"
