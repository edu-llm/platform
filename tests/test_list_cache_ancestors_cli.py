from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.list_cache_ancestors import list_ancestors, main


def git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def history(tmp_path: Path) -> Path:
    """A three-commit line, oldest first, with committer identity pinned locally."""
    repository = tmp_path / "source"
    repository.mkdir()
    git(repository, "init", "--initial-branch=main", "--quiet")
    git(repository, "config", "user.email", "nobody@example.invalid")
    git(repository, "config", "user.name", "Nobody")
    for index in range(3):
        (repository / "file.txt").write_text(f"{index}\n", encoding="utf-8")
        git(repository, "add", "file.txt")
        git(repository, "commit", "--quiet", "-m", f"commit {index}")
    return repository


def head(repository: Path) -> str:
    return git(repository, "rev-parse", "HEAD")


def argv(repository: Path, output: Path, **overrides: str) -> list[str]:
    arguments: dict[str, str] = {
        "--repository-root": str(repository),
        "--commit-sha": head(repository),
        "--github-output": str(output),
    }
    arguments.update(overrides)
    return [token for pair in arguments.items() for token in pair]


def test_the_commit_under_build_is_not_offered_as_its_own_ancestor(history: Path) -> None:
    # Its image does not exist: the pre-flight lookup already established the tag is
    # unpublished, which is the only reason this step runs at all.
    ancestors, reason = list_ancestors(history, head(history))

    assert reason is None
    assert head(history)[:12] not in ancestors


def test_ancestors_come_back_nearest_first(history: Path) -> None:
    walk = git(history, "rev-list", "HEAD").splitlines()
    ancestors, reason = list_ancestors(history, head(history))

    assert reason is None
    assert ancestors == (walk[1][:12], walk[2][:12])


def test_a_root_commit_has_no_ancestors_and_no_error(tmp_path: Path) -> None:
    # `<sha>^` on a root commit is a git failure, which would read here as a broken
    # checkout. The walk starts at the commit and drops it instead, so the first build of
    # a repository is an ordinary empty answer.
    repository = tmp_path / "root"
    repository.mkdir()
    git(repository, "init", "--initial-branch=main", "--quiet")
    git(repository, "config", "user.email", "nobody@example.invalid")
    git(repository, "config", "user.name", "Nobody")
    (repository / "file.txt").write_text("only\n", encoding="utf-8")
    git(repository, "add", "file.txt")
    git(repository, "commit", "--quiet", "-m", "root")

    ancestors, reason = list_ancestors(repository, head(repository))

    assert ancestors == ()
    assert reason is not None and reason.value == "no_ancestor_commits"


def test_a_checkout_that_is_not_a_repository_is_a_slow_build_and_not_a_failed_one(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A publish that stopped because an optimisation could not be arranged would be a
    # worse outcome than the cost it saves.
    output = tmp_path / "step-output.txt"
    exit_code = main(
        [
            "--repository-root",
            str(tmp_path / "absent"),
            "--commit-sha",
            "a" * 40,
            "--github-output",
            str(output),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "history_unreadable" in captured.err
    assert output.read_text(encoding="utf-8") == "cache_ancestor_tags=\n"


def test_the_reason_is_printed_rather_than_left_to_be_inferred_from_a_slow_build(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A cache that quietly stopped working shows up as a bill months later.
    main(
        [
            "--repository-root",
            str(tmp_path / "absent"),
            "--commit-sha",
            "a" * 40,
            "--github-output",
            str(tmp_path / "step-output.txt"),
        ]
    )

    assert "shares with nothing" in capsys.readouterr().err


def test_the_step_output_is_one_space_separated_line(history: Path, tmp_path: Path) -> None:
    output = tmp_path / "step-output.txt"

    assert main(argv(history, output)) == 0

    line = output.read_text(encoding="utf-8")
    assert line.startswith("cache_ancestor_tags=")
    assert line.endswith("\n")
    assert len(line.splitlines()) == 1
    assert all(len(tag) == 12 for tag in line.split("=", 1)[1].split())


def test_step_outputs_are_appended_rather_than_truncated(history: Path, tmp_path: Path) -> None:
    output = tmp_path / "step-output.txt"
    output.write_text("previous=kept\n", encoding="utf-8")

    assert main(argv(history, output)) == 0
    assert output.read_text(encoding="utf-8").startswith("previous=kept\n")


def test_the_limit_bounds_how_far_back_the_walk_goes(history: Path, tmp_path: Path) -> None:
    output = tmp_path / "step-output.txt"

    assert main(argv(history, output, **{"--limit": "1"})) == 0
    assert len(output.read_text(encoding="utf-8").split("=", 1)[1].split()) == 1


def test_a_negative_limit_is_a_defect_rather_than_an_empty_answer(
    history: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "step-output.txt"

    assert main(argv(history, output, **{"--limit": "-1"})) == 2
    assert capsys.readouterr().err.strip() == "invalid_ancestor_limit"
    assert not output.exists()


def test_only_the_first_parent_line_is_walked(tmp_path: Path) -> None:
    # On a merge the second parent is a different line of development. Its images are no
    # nearer to this tree for being reachable, and walking them would offer a longer list
    # whose extra entries are worse cache sources than the ones they displace.
    repository = tmp_path / "merged"
    repository.mkdir()
    git(repository, "init", "--initial-branch=main", "--quiet")
    git(repository, "config", "user.email", "nobody@example.invalid")
    git(repository, "config", "user.name", "Nobody")
    (repository / "file.txt").write_text("base\n", encoding="utf-8")
    git(repository, "add", "file.txt")
    git(repository, "commit", "--quiet", "-m", "base")
    base = head(repository)

    git(repository, "checkout", "--quiet", "-b", "side")
    (repository / "side.txt").write_text("side\n", encoding="utf-8")
    git(repository, "add", "side.txt")
    git(repository, "commit", "--quiet", "-m", "side")
    side = head(repository)

    git(repository, "checkout", "--quiet", "main")
    (repository / "file.txt").write_text("main\n", encoding="utf-8")
    git(repository, "add", "file.txt")
    git(repository, "commit", "--quiet", "-m", "main")
    mainline = head(repository)

    git(repository, "merge", "--quiet", "--no-ff", "-m", "merge", "side")

    ancestors, reason = list_ancestors(repository, head(repository))

    assert reason is None
    assert ancestors == (mainline[:12], base[:12])
    assert side[:12] not in ancestors
