"""Fixtures for the CLI tests, and the one property they all rest on.

**NOTHING HERE REACHES A NETWORK, A CREDENTIAL OR AWS, AND THAT IS ENFORCED RATHER THAN
INTENDED.** :class:`FakeRunner` answers only the commands a test has written an answer
for and raises on anything else, so a change that made the CLI shell out to something new
fails here rather than passing quietly on a laptop where the tool happens to exist. It also
means an ``aws`` call added anywhere on this path is a test failure by construction, which
is the property worth having: this binary is a facade over two workflows and has no
business holding a cloud credential.

The reviewed configuration is the real ``config/`` in this repository rather than a fixture
copy, for the reason ``tests/test_compile_submission_cli.py`` gives about the same choice:
the values that decide a submission's fate are in those files, and a fixture copy would be
a second answer to every question they settle.
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

import pytest

from edullm_platform.cli.main import main
from edullm_platform.cli.workspace import CommandResult

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"

#: Somebody the roster names on exactly one declared group, so team resolution has a single
#: answer and a test about anything else is not also a test about team resolution.
#: ``config/organization.yaml`` puts caiiris on memory-split and on scratch.
SUBMITTER = "caiiris"
SUBMITTER_TEAM = "memory-split"

#: A submitter the roster puts on two declared groups, which is what makes team resolution
#: ambiguous. ``alphaxia2100`` is one of the seven ``decisions.md`` counts.
SUBMITTER_ON_TWO_TEAMS = "alphaxia2100"

COMMIT = "8076c077533eb79742f4ed22aade439df123a593"

#: A command that satisfies both command guards on a one-device profile: it names a program,
#: it keeps its quoting through a shell wrapper, and the checkpoint directory expands.
TRAINING_COMMAND = (
    "bash -lc 'python .edullm/train_on_corpus.py \"$EDULLM_RUN_ID\" "
    '--save-folder "$EDULLM_CHECKPOINT_DIR"\''
)


class UnexpectedCommandError(AssertionError):
    """The CLI ran something no test told it how to answer."""


class FakeRunner:
    """Answers the commands a test declares, and refuses to invent one it did not.

    Matched on a prefix of the argv rather than on the whole of it, because the interesting
    part of most of these calls is the first three or four words and the rest is a path in a
    temporary directory. Longest prefix wins, so a test can answer ``git rev-parse HEAD``
    differently from ``git rev-parse --show-toplevel``.
    """

    def __init__(self, answers: Mapping[tuple[str, ...], CommandResult | Callable[[tuple[str, ...]], CommandResult]]) -> None:
        self._answers = dict(answers)
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv: tuple[str, ...], *, cwd: Path | None = None) -> CommandResult:
        self.calls.append(argv)
        matches = [prefix for prefix in self._answers if argv[: len(prefix)] == prefix]
        if not matches:
            raise UnexpectedCommandError(
                f"no answer was declared for {' '.join(argv)}. Every command this CLI runs "
                "has to be one a test knew about, which is what keeps the suite off the "
                "network and away from AWS."
            )
        answer = self._answers[max(matches, key=len)]
        return answer(argv) if callable(answer) else answer

    def ran(self, *prefix: str) -> list[tuple[str, ...]]:
        return [argv for argv in self.calls if argv[: len(prefix)] == tuple(prefix)]


def ok(stdout: str = "") -> CommandResult:
    return CommandResult(returncode=0, stdout=stdout, stderr="")


def failed(stderr: str = "", returncode: int = 1) -> CommandResult:
    return CommandResult(returncode=returncode, stdout="", stderr=stderr)


def git_answers(
    root: Path,
    *,
    repository: str = "OLMo-core",
    commit: str = COMMIT,
    dirty: Iterable[str] = (),
    pushed: bool = True,
) -> dict[tuple[str, ...], CommandResult]:
    """What ``read_git_facts`` asks git, answered as a clean pushed checkout by default."""
    return {
        ("git", "rev-parse", "--show-toplevel"): ok(f"{root}\n"),
        ("git", "rev-parse", "HEAD"): ok(f"{commit}\n"),
        ("git", "rev-parse", "--abbrev-ref", "HEAD"): ok("edullm/an-arm\n"),
        ("git", "remote", "get-url", "origin"): ok(
            f"git@github.com:edu-llm/{repository}.git\n"
        ),
        ("git", "status", "--porcelain"): ok(
            "".join(f" M {path}\n" for path in dirty)
        ),
        ("git", "branch", "--remotes", "--contains"): ok(
            "  origin/edullm/an-arm\n" if pushed else ""
        ),
    }


def write_spec(
    root: Path,
    *,
    workload: str = "olmo-core-train",
    compute: str | None = "gpu-1xa10g",
    command: str = TRAINING_COMMAND,
    fanout: tuple[int, str] | None = None,
) -> Path:
    path = root / ".edullm" / "run.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["schema_version: 1", f"workload_profile: {workload}"]
    if compute is not None:
        lines.append(f"suggested_compute: {compute}")
    lines.append(f"command: {json.dumps(command)}")
    if fanout is not None:
        lines.extend(["fanout:", f"  size: {fanout[0]}", f"  index_parameter: {fanout[1]}"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def invoke(
    argv: list[str],
    *,
    runner: FakeRunner,
    cwd: Path,
    monkeypatch: pytest.MonkeyPatch,
    login: str | None = SUBMITTER,
) -> tuple[int, str, str]:
    """Run the CLI as a person would, with both streams captured and no ambient identity.

    ``GH_CONFIG_DIR`` is pointed at an empty directory on every path, including the one
    where a login is declared. Without it a suite run on a laptop with ``gh auth login``
    already done would read that person's login out of their home directory, and the test
    for "nobody is logged in" would pass or fail depending on whose machine it ran on.
    """
    monkeypatch.setenv("GH_CONFIG_DIR", str(cwd / "_no-gh-config"))
    if login is None:
        monkeypatch.delenv("EDULLM_GITHUB_LOGIN", raising=False)
    else:
        monkeypatch.setenv("EDULLM_GITHUB_LOGIN", login)
    out, err = io.StringIO(), io.StringIO()
    code = main(
        ["--config-dir", str(CONFIG_DIR), *argv],
        runner=runner,
        out=out,
        err=err,
        cwd=cwd,
    )
    return code, out.getvalue(), err.getvalue()
