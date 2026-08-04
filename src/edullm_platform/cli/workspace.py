"""What the researcher's own checkout and their ``gh`` login say, read locally.

TWO PROGRAMS ARE SHELLED OUT TO AND NEITHER IS ``aws``. ``git`` answers which repository
this is, which commit is checked out, whether the tree is clean and whether the commit is
anywhere a build could have seen it. ``gh`` carries the credential, because
``docs-frank/reference/system-overview.md`` puts the submission behind ``gh workflow run``
and the trust policy pins the credential to the workflow file rather than to a person --
so there is no credential for this binary to hold and nothing for it to ask a researcher
to configure.

**Every call goes through :class:`CommandRunner` so the suite can be hermetic.** A test
that shelled out for real would need a git repository, a GitHub token and a network, and
the first two are the cheap half of that problem. The interesting behaviour here is what
the CLI concludes from an answer, so the answers are supplied.

**The identity is read out of ``gh``'s own configuration before it is asked for over the
network.** ``check`` promises to refuse in under a second without touching anything, and
an off-roster submitter is the first refusal it makes, so the login has to be free. ``gh``
writes it into ``hosts.yml`` at login; asking the API is the fallback for a configuration
laid out somewhere this does not expect.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

__all__ = [
    "CommandResult",
    "CommandRunner",
    "GitFacts",
    "SubprocessRunner",
    "ToolMissingError",
    "github_login",
    "read_git_facts",
    "repository_name_from_remote",
]

LOGIN_VARIABLE = "EDULLM_GITHUB_LOGIN"


class ToolMissingError(RuntimeError):
    """A program the CLI drives is not on PATH.

    Its own type because the remedy is an installation rather than an edit, and a message
    about a missing ``gh`` printed as though it were a refusal sends somebody to look at
    their spec.
    """


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def text(self) -> str:
        return self.stdout.strip()


class CommandRunner(Protocol):
    def __call__(
        self, argv: tuple[str, ...], *, cwd: Path | None = None
    ) -> CommandResult: ...


class SubprocessRunner:
    """The real one. Captures both streams, never raises on a non-zero exit.

    Non-raising because almost every call here has a meaningful failure: a directory that
    is not a git repository, a commit no remote carries, a ``gh`` that is not logged in.
    Each of those is an answer this CLI turns into a sentence, and an exception would make
    the caller write the same try block six times.
    """

    def __call__(self, argv: tuple[str, ...], *, cwd: Path | None = None) -> CommandResult:
        if shutil.which(argv[0]) is None:
            raise ToolMissingError(
                f"{argv[0]} is not on PATH. edullm drives git and gh rather than holding a "
                "credential of its own, so both have to be installed and gh has to be "
                "logged in: gh auth login."
            )
        completed = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            cwd=None if cwd is None else str(cwd),
            check=False,
        )
        return CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


@dataclass(frozen=True)
class GitFacts:
    """Everything the submission needs from the working tree, and how sure it is.

    ``commit_sha`` is the resolved forty characters rather than a branch name, deliberately.
    The workflow resolves a ref for somebody who typed the only name they know their work
    by, and it says why in its own comment: a branch moves and a record must not. Sending
    the resolved commit means the record names what was checked out at the moment of
    submitting rather than whatever the branch points at when the runner picks it up.
    """

    root: Path | None
    repository: str | None
    branch: str | None
    commit_sha: str | None
    #: Paths git reports as modified, added, deleted or untracked, capped for a message.
    dirty_paths: tuple[str, ...]
    #: Whether any remote-tracking ref in this clone contains the commit. False is not proof
    #: that nothing was pushed -- a clone that has not fetched since the push says the same
    #: thing -- which is why the refusal it produces names ``git fetch`` beside ``git push``.
    commit_on_a_remote: bool

    @property
    def is_a_repository(self) -> bool:
        return self.root is not None


def read_git_facts(runner: CommandRunner, *, cwd: Path) -> GitFacts:
    root = runner(("git", "rev-parse", "--show-toplevel"), cwd=cwd)
    if not root.ok:
        return GitFacts(
            root=None,
            repository=None,
            branch=None,
            commit_sha=None,
            dirty_paths=(),
            commit_on_a_remote=False,
        )
    top = Path(root.text)
    remote = runner(("git", "remote", "get-url", "origin"), cwd=top)
    head = runner(("git", "rev-parse", "HEAD"), cwd=top)
    branch = runner(("git", "rev-parse", "--abbrev-ref", "HEAD"), cwd=top)
    status = runner(("git", "status", "--porcelain"), cwd=top)
    commit_sha = head.text if head.ok and len(head.text) == 40 else None
    contains = (
        runner(("git", "branch", "--remotes", "--contains", commit_sha), cwd=top)
        if commit_sha is not None
        else CommandResult(returncode=1, stdout="", stderr="")
    )
    return GitFacts(
        root=top,
        # The remote decides the repository name rather than the directory, because a clone
        # can be named anything and `config/repositories.yaml` is keyed on the GitHub name.
        repository=repository_name_from_remote(remote.text) if remote.ok else None,
        branch=branch.text if branch.ok and branch.text != "HEAD" else None,
        commit_sha=commit_sha,
        dirty_paths=tuple(
            line[3:] for line in status.stdout.splitlines() if len(line) > 3
        )
        if status.ok
        else (),
        commit_on_a_remote=contains.ok and bool(contains.text),
    )


def repository_name_from_remote(url: str) -> str | None:
    """``git@github.com:edu-llm/OLMo-core.git`` and the https spelling both give OLMo-core.

    The last path segment with any ``.git`` suffix removed, which is the whole rule and
    holds for both transports. Returns ``None`` for an empty remote rather than an empty
    string, so a caller cannot accidentally look up the repository named "".
    """
    trimmed = url.strip().removesuffix("/")
    if not trimmed:
        return None
    tail = trimmed.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    name = tail.removesuffix(".git")
    return name or None


def github_login(
    runner: CommandRunner,
    *,
    environ: Mapping[str, str] | None = None,
    config_home: Path | None = None,
    allow_network: bool = False,
) -> str | None:
    """Who ``gh`` says you are, for free where possible.

    ``allow_network`` is the switch between the two halves of this binary. ``check``
    promises to answer without asking anything, so it reads the file ``gh`` wrote at login
    and reports nobody when there is none. The verbs that were going to reach GitHub anyway
    may ask, because for them the call costs nothing extra.
    """
    variables = os.environ if environ is None else environ
    declared = variables.get(LOGIN_VARIABLE, "").strip()
    if declared:
        return declared
    from_config = _login_from_gh_config(variables, config_home)
    if from_config is not None:
        return from_config
    if not allow_network:
        return None
    answered = runner(("gh", "api", "user", "--jq", ".login"))
    return answered.text or None


def _login_from_gh_config(
    variables: Mapping[str, str], config_home: Path | None
) -> str | None:
    """``gh``'s own ``hosts.yml``, at whichever location that program would read it from.

    ``GH_CONFIG_DIR`` overrides rather than being tried first and then fallen back from,
    which is ``gh``'s own semantics and is also what makes this testable: a directory
    declared and empty has to mean "nobody is logged in", not "look in the home directory
    of whoever is running the suite".
    """
    import yaml

    if config_home is not None:
        directory = config_home
    else:
        declared = variables.get("GH_CONFIG_DIR", "").strip()
        if declared:
            directory = Path(declared)
        else:
            xdg = variables.get("XDG_CONFIG_HOME", "").strip()
            directory = Path(xdg) / "gh" if xdg else Path.home() / ".config" / "gh"
    hosts = directory / "hosts.yml"
    if not hosts.is_file():
        return None
    try:
        document = yaml.safe_load(hosts.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(document, dict):
        return None
    entry = document.get("github.com")
    if not isinstance(entry, dict):
        return None
    user = entry.get("user")
    return user if isinstance(user, str) and user else None
