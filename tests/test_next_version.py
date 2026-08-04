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
    read_version,
    rewrite_version,
)

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_the_patch_component_is_what_moves() -> None:
    assert next_patch_version("0.2.0") == "0.2.1"
    assert next_patch_version("0.2.9") == "0.2.10"
    assert next_patch_version("1.0.0") == "1.0.1"


@pytest.mark.parametrize("version", ["0.2", "0.2.0rc1", "0.2.0+local", "v0.2.0", "2026.08.04.1"])
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


def test_reading_and_bumping_are_the_two_things_the_workflow_asks_for(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The exact two invocations in ``release-tag.yml``, including that a read writes nothing."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "x"\nversion = "0.2.0"\n', encoding="utf-8")

    assert main(["--pyproject", str(pyproject)]) == 0
    assert capsys.readouterr().out == "0.2.0\n"
    assert read_version(pyproject.read_text(encoding="utf-8")) == "0.2.0"

    assert main(["--pyproject", str(pyproject), "--bump"]) == 0
    assert capsys.readouterr().out == "0.2.1\n"
    assert read_version(pyproject.read_text(encoding="utf-8")) == "0.2.1"


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
