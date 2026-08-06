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
a second answer to every question they settle. It is reached by ``--config-dir``, which is an
absolute path, and never by the process finding it.

**AND THE PROCESS IS PUT IN THE TEMPORARY DIRECTORY IT IS TOLD ABOUT, WHICH IT WAS NOT UNTIL
2026-08-06.** ``invoke`` handed ``main`` a ``cwd`` and left the interpreter's own working
directory where pytest started it, which is the root of this repository. So every relative
path the CLI resolved -- ``config/reports/working-tier.yaml`` among them -- found a platform
checkout under it, in the suite and only in the suite. ``edullm run`` and ``edullm shell``
shipped and had never worked anywhere else, with 207 test modules green behind them, because
the one condition that would have shown it was the condition the suite could not produce.

:func:`invoke` now chdirs, so a verb that reads a file relative to the working directory
reads it out of an empty temporary directory here exactly as it would on a researcher's
laptop. Nothing was moved to make that safe: every fixture in this suite is reached through
``PROJECT_ROOT``, which is absolute and computed from ``__file__``.
"""

from __future__ import annotations

import io
import json
import os
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

import pytest

from edullm_platform.cli.lane import SESSION_PLUGIN
from edullm_platform.cli.main import main
from edullm_platform.cli.preferences import DEFAULT_TEAM_FILE, PREFERENCES_DIRECTORY
from edullm_platform.cli.workspace import CommandResult
from edullm_platform.researcher_lane import EXPIRES_AT_TAG_KEY

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
        #: Beside ``calls`` rather than zipped into it, because a case about the lane wants the
        #: credential a call carried and every case that came before wants only the argv.
        self.environments: list[dict[str, str]] = []

    def __call__(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path | None = None,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        self.calls.append(argv)
        self.environments.append(dict(env or {}))
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


#: The account id every lane fixture answers with. AWS reserves it for documentation, and
#: tests/test_evidence.py scans the tracked tree for anything shaped like a real one and allows
#: exactly this. Twelve zeroes is rejected there, so a fixture cannot quietly use one.
FAKE_ACCOUNT = "123456789012"

LANE_INSTANCE = "i-0000000000000aaaa"

#: The expiry a reused fixture machine carries on its tag. Deliberately a round instant that no
#: arithmetic against the test clock produces, so a verb that computed one instead of reading
#: this cannot match it by coincidence.
LANE_EXISTING_EXPIRY = "2026-08-06T09:00:00Z"


def lane_answers(
    *,
    existing: str | None = None,
    existing_expiry: str | None = LANE_EXISTING_EXPIRY,
    remote_exit: int | None = 0,
    agent: str = "Online",
) -> dict[tuple[str, ...], CommandResult]:
    """Every AWS call a lane verb makes, answered as a laptop already holding a session.

    ``remote_exit`` of ``None`` is a session that dropped: the stream carries output and no
    sentinel, which is what a Spot interruption in the middle of a command looks like.

    ``describe-instances`` answers the shape :func:`~edullm_platform.cli.lane.find_machine_argv`
    asks for, an instance id beside its tag list, rather than the bare id it asked for until
    2026-08-06. That is the whole seam the stale-expiry defect lived in: the fixture and the
    account have to agree about the answer's shape or a test proves nothing about a laptop.
    ``existing_expiry`` of ``None`` is a machine carrying no such tag, which is a machine
    launched before the tag existed or one somebody stripped.

    Which repository the caller is standing in is :func:`git_answers`' business and not this
    one's. It changes no AWS answer, and the case the whole slice turns on -- a directory nothing
    registers -- is expressed by passing it there.
    """
    sentinel = "" if remote_exit is None else f"\nedullm-exit:{remote_exit}\n"
    return {
        ("aws", "sts", "get-caller-identity"): ok(
            json.dumps(
                {
                    "Account": FAKE_ACCOUNT,
                    "Arn": (
                        f"arn:aws:sts::{FAKE_ACCOUNT}:assumed-role/Intern-caiiris-sbsandbox"
                        "/broker-caiiris-1785873426"
                    ),
                }
            )
        ),
        ("aws", "sts", "assume-role"): ok(
            json.dumps(
                {
                    "Credentials": {
                        "AccessKeyId": "AKIAEXAMPLE",
                        "SecretAccessKey": "secret",
                        "SessionToken": "token",
                        "Expiration": "2026-08-06T00:00:00Z",
                    }
                }
            )
        ),
        ("aws", "ssm", "get-parameter"): ok("ami-000000000000000aa\n"),
        ("aws", "ec2", "describe-subnets"): ok(json.dumps(["subnet-000000000000000bb"])),
        ("aws", "ec2", "describe-security-groups"): ok(json.dumps(["sg-000000000000000cc"])),
        ("aws", "ec2", "describe-instances"): ok(
            json.dumps(
                [
                    {
                        "machine": existing,
                        "tags": [{"Key": "Project", "Value": "mixlaw"}]
                        + (
                            [{"Key": EXPIRES_AT_TAG_KEY, "Value": existing_expiry}]
                            if existing_expiry
                            else []
                        ),
                    }
                ]
                if existing
                else []
            )
        ),
        ("aws", "ec2", "run-instances"): ok(f"{LANE_INSTANCE}\n"),
        ("aws", "ssm", "describe-instance-information"): ok(f"{agent}\n"),
        ("aws", "s3", "sync"): ok(""),
        ("aws", "ssm", "start-session"): ok(f"hello from the machine{sentinel}"),
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


def config_home(cwd: Path) -> Path:
    """The XDG config home :func:`invoke` gives one run, which is per-test and empty."""
    return cwd / "_no-config-home"


def default_team_path(cwd: Path) -> Path:
    """Where a personal default would live under that config home.

    Composed from the module's own constants rather than spelled here, so renaming the
    directory or the file moves the tests with it rather than leaving them asserting against
    a path nothing reads.
    """
    return config_home(cwd) / PREFERENCES_DIRECTORY / DEFAULT_TEAM_FILE


def write_default_team(cwd: Path, contents: str) -> Path:
    """Put a personal default where this run will find it, exactly as a researcher would.

    Takes the whole file contents rather than a team id, because half of what is worth
    testing here is what the reader does with a file somebody typed by hand: a trailing
    newline, a blank first line, something left on a second line.
    """
    path = default_team_path(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    return path


def invoke(
    argv: list[str],
    *,
    runner: FakeRunner,
    cwd: Path,
    monkeypatch: pytest.MonkeyPatch,
    login: str | None = SUBMITTER,
    config_dir: Path = CONFIG_DIR,
    plugin: bool = True,
) -> tuple[int, str, str]:
    """Run the CLI as a person would, with both streams captured and no ambient identity.

    ``GH_CONFIG_DIR`` is pointed at an empty directory on every path, including the one
    where a login is declared. Without it a suite run on a laptop with ``gh auth login``
    already done would read that person's login out of their home directory, and the test
    for "nobody is logged in" would pass or fail depending on whose machine it ran on.

    ``XDG_CONFIG_HOME`` is pointed at a second empty directory for the same reason, one layer
    along. The personal default team lives under the config home, so a maintainer who has set
    one for their own submissions would otherwise have every team assertion in this suite
    answer with their preference instead of with the roster. A test that wants a default calls
    :func:`write_default_team` first, which writes it into this same directory.

    ``--config-dir`` goes after the verb because that is where it lives: the root parser
    takes no option carrying a value, which is what lets a first word be read as a verb
    without parsing, and is what lets a retired name be answered with its replacement
    rather than with argparse's list of choices.

    ``config_dir`` is this repository's own ``config/`` unless a test says otherwise, for the
    reason at the top of this module. The override exists for the one question that is about
    the directory rather than about its contents: ``check`` now names which reviewed
    configuration answered, and a case asserting that has to be able to point it somewhere it
    can recognise in the output.

    ``plugin`` puts a Session Manager plugin on PATH, or keeps one off it, and is the same kind
    of measure as the two directories above. The lane verbs check for it with ``shutil.which``,
    which reads the developer's own laptop: without this, whether the lane cases pass would
    depend on whether that laptop has the plugin installed, and the case that asserts the
    refusal would fail on a laptop that does. PATH is prepended rather than replaced, because
    the runner is a fake and the ``git`` this suite does not shell out to is still wanted by
    anything that looks.
    """
    # THE PROCESS GOES WHERE THE CALLER SAYS THE PERSON IS STANDING. The header says what one
    # line of this bought and what its absence cost. It is deliberately not conditional: a
    # case that wanted the repository under its feet would be a case testing the CLI in a
    # condition no researcher is ever in.
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("GH_CONFIG_DIR", str(cwd / "_no-gh-config"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home(cwd)))
    tools = cwd / "_tools"
    tools.mkdir(exist_ok=True)
    if plugin:
        stub = tools / SESSION_PLUGIN
        stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)
        monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ['PATH']}")
    else:
        # THE WHOLE PATH AND NOT A PREPEND, because a prepend cannot hide a plugin that is
        # further along it. Nothing under test shells out for real -- the runner is a fake --
        # so an empty PATH answers the one question this branch is asking.
        monkeypatch.setenv("PATH", str(tools))
    if login is None:
        monkeypatch.delenv("EDULLM_GITHUB_LOGIN", raising=False)
    else:
        monkeypatch.setenv("EDULLM_GITHUB_LOGIN", login)
    out, err = io.StringIO(), io.StringIO()
    verb, *rest = argv
    code = main(
        [verb, "--config-dir", str(config_dir), *rest],
        runner=runner,
        out=out,
        err=err,
        cwd=cwd,
    )
    return code, out.getvalue(), err.getvalue()
